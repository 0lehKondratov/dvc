import os
import uuid
import json
import shutil
import filecmp

from dvc.system import System
from dvc.remote.base import RemoteBase, STATUS_MAP
from dvc.state import State
from dvc.logger import Logger
from dvc.utils import remove, move, copyfile, file_md5, to_chunks
from dvc.config import Config
from dvc.exceptions import DvcException
from dvc.progress import progress
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor


class RemoteLOCAL(RemoteBase):
    scheme = ''
    REGEX = r'^(?P<path>(/+|.:\\+).*)$'
    PARAM_MD5 = State.PARAM_MD5
    PARAM_RELPATH = State.PARAM_RELPATH

    CACHE_TYPES = ['reflink', 'hardlink', 'symlink', 'copy']
    CACHE_TYPE_MAP = {
        'copy': shutil.copyfile,
        'symlink': System.symlink,
        'hardlink': System.hardlink,
        'reflink': System.reflink,
    }

    def __init__(self, project, config):
        self.project = project
        self.state = self.project.state
        self.link_state = project.link_state
        storagepath = config.get(Config.SECTION_AWS_STORAGEPATH, None)
        self.cache_dir = config.get(Config.SECTION_REMOTE_URL, storagepath)

        types = config.get(Config.SECTION_CACHE_TYPE, None)
        if types:
            if isinstance(types, str):
                types = [t.strip() for t in types.split(',')]
            self.cache_types = types
        else:
            self.cache_types = self.CACHE_TYPES

        if self.cache_dir != None and not os.path.exists(self.cache_dir):
            os.mkdir(self.cache_dir)

    @property
    def prefix(self):
        return self.cache_dir

    def all(self):
        clist = []
        for entry in os.listdir(self.cache_dir):
            subdir = os.path.join(self.cache_dir, entry)
            if not os.path.isdir(subdir):
                continue

            for cache in os.listdir(subdir):
                path = os.path.join(subdir, cache)
                clist.append(self.path_to_md5(path))

        return clist

    def get(self, md5):
        if not md5:
            return None

        return os.path.join(self.cache_dir, md5[0:2], md5[2:])

    def path_to_md5(self, path):
        relpath = os.path.relpath(path, self.cache_dir)
        return os.path.dirname(relpath) + os.path.basename(relpath)

    def changed(self, md5):
        cache = self.get(md5)
        if self.state.changed(cache, md5=md5):
            if os.path.exists(cache):
                Logger.warn('Corrupted cache file {}'.format(os.path.relpath(cache)))
                remove(cache)
            return True

        return False

    def link(self, cache, path):
        assert os.path.isfile(cache)

        dname = os.path.dirname(path)
        if not os.path.exists(dname):
            os.makedirs(dname)

        i = len(self.cache_types)
        while i > 0:
            try:
                self.CACHE_TYPE_MAP[self.cache_types[0]](cache, path)
                return
            except Exception as exc:
                msg = 'Cache type \'{}\' is not supported'.format(self.cache_types[0])
                Logger.debug(msg)
                del self.cache_types[0]
                i -= 1

        raise DvcException('No possible cache types left to try out.')

    def load_dir_cache(self, md5):
        path = self.get(md5)

        assert self.is_dir_cache(path)

        try:
            with open(path, 'r') as fd:
                d = json.load(fd)
        except Exception as exc:
            msg = u'Failed to load dir cache \'{}\''
            Logger.error(msg.format(os.path.relpath(path)), exc)
            return []

        if not isinstance(d, list):
            msg = u'Dir cache file format error \'{}\': skipping the file'
            Logger.error(msg.format(os.path.relpath(path)))
            return []

        return d

    def dump_dir_cache(self, md5, dir_info):
        path = self.get(md5)
        dname = os.path.dirname(path)

        assert self.is_dir_cache(path)
        assert isinstance(dir_info, list)

        if not os.path.isdir(dname):
            os.makedirs(dname)

        # NOTE: Writing first and renaming after that
        # to make sure that the operation is atomic.
        tmp = '{}.{}'.format(path, str(uuid.uuid4()))
        with open(tmp, 'w+') as fd:
            json.dump(dir_info, fd, sort_keys=True)
        move(tmp, path)

    @staticmethod
    def is_dir_cache(cache):
        return cache.endswith(State.MD5_DIR_SUFFIX)

    def checkout(self, path_info, checksum_info):
        path = path_info['path']
        md5 = checksum_info.get(self.PARAM_MD5, None)
        cache = self.get(md5)

        if not cache:
            Logger.warn('No cache info for \'{}\'. Skipping checkout.'.format(os.path.relpath(path)))
            return

        if self.changed(md5):
            msg = u'Cache \'{}\' not found. File \'{}\' won\'t be created.'
            Logger.warn(msg.format(md5, os.path.relpath(path)))
            remove(path)
            return

        if os.path.exists(path):
            msg = u'Data \'{}\' exists. Removing before checkout'
            Logger.debug(msg.format(os.path.relpath(path)))
            remove(path)

        msg = u'Checking out \'{}\' with cache \'{}\''
        Logger.debug(msg.format(os.path.relpath(path), md5))

        if not self.is_dir_cache(cache):
            self.link(cache, path)
            self.link_state.update(path)
            return

        # Create dir separately so that dir is created
        # even if there are no files in it
        if not os.path.exists(path):
            os.makedirs(path)

        for entry in self.load_dir_cache(md5):
            md5 = entry[self.PARAM_MD5]
            c = self.get(md5)
            relpath = entry[self.PARAM_RELPATH]
            p = os.path.join(path, relpath)
            self.link(c, p)
        self.link_state.update(path)

    def _move(self, inp, outp):
        # moving in two stages to make last the move atomic in
        # case inp and outp are in different filesystems
        tmp = '{}.{}'.format(outp, str(uuid.uuid4()))
        move(inp, tmp)
        move(tmp, outp)

    def _save_file(self, path_info):
        path = path_info['path']
        md5 = self.state.update(path)
        assert md5 != None

        cache = self.get(md5)

        if self.changed(md5):
            self._move(path, cache)
        else:
            remove(path)

        self.link(cache, path)
        self.link_state.update(path)

        return {self.PARAM_MD5: md5}

    def _save_dir(self, path_info):
        path = path_info['path']
        md5, dir_info = self.state.update_info(path)

        for entry in dir_info:
            relpath = entry[State.PARAM_RELPATH]
            m = entry[State.PARAM_MD5]
            p = os.path.join(path, relpath)
            c = self.get(m)

            if self.changed(m):
                self._move(p, c)
            else:
                remove(p)

            self.link(c, p)

        self.link_state.update(path)

        return {self.PARAM_MD5: md5}

    def save(self, path_info):
        if path_info['scheme'] != 'local':
            raise NotImplementedError

        path = path_info['path']

        if os.path.isdir(path):
            return self._save_dir(path_info)
        else:
            return self._save_file(path_info)

    def save_info(self, path_info):
        if path_info['scheme'] != 'local':
            raise NotImplementedError

        return {self.PARAM_MD5: self.state.update(path_info['path'])}

    def remove(self, path_info):
        if path_info['scheme'] != 'local':
            raise NotImplementedError

        remove(path_info['path'])

    def move(self, from_info, to_info):
        if from_info['scheme'] != 'local' or to_info['scheme'] != 'local':
            raise NotImplementedError

        move(from_info['path'], to_info['path'])

    def md5s_to_path_infos(self, md5s):
        return [{'scheme': 'local',
                 'path': os.path.join(self.prefix, md5[0:2], md5[2:])} for md5 in md5s]

    def exists(self, path_infos):
        ret = []
        for path_info in path_infos:
            ret.append(os.path.exists(path_info['path']))
        return ret

    def upload(self, from_infos, to_infos, names=None):
        names = self._verify_path_args(to_infos, from_infos, names)

        for from_info, to_info, name in zip(from_infos, to_infos, names):
            if to_info['scheme'] != 'local':
                raise NotImplementedError

            if from_info['scheme'] != 'local':
                raise NotImplementedError

            Logger.debug("Uploading '{}' to '{}'".format(from_info['path'], to_info['path']))

            if not name:
                name = os.path.basename(from_info['path'])

            self._makedirs(to_info['path'])

            try:
                copyfile(from_info['path'], to_info['path'], name=name)
            except Exception as exc:
                Logger.error("Failed to upload '{}' tp '{}'".format(from_info['path'], to_info['path']), exc)

    def download(self, from_infos, to_infos, no_progress_bar=False, names=None):
        names = self._verify_path_args(from_infos, to_infos, names)

        for to_info, from_info, name in zip(to_infos, from_infos, names):
            if from_info['scheme'] != 'local':
                raise NotImplementedError

            if to_info['scheme'] != 'local':
                raise NotImplementedError

            Logger.debug("Downloading '{}' to '{}'".format(from_info['path'], to_info['path']))

            if not name:
                name = os.path.basename(to_info['path'])

            self._makedirs(to_info['path'])
            tmp_file = self.tmp_file(to_info['path'])
            try:
                copyfile(from_info['path'], tmp_file, no_progress_bar=no_progress_bar, name=name)
            except Exception as exc:
                Logger.error("Failed to download '{}' to '{}'".format(from_info['path'], to_info['path']), exc)
                continue

            os.rename(tmp_file, to_info['path'])

    def _collect(self, checksum_infos):
        missing = []
        collected = []
        for info in checksum_infos:
            md5 = info[self.PARAM_MD5]
            cache = self.get(md5)
            if not self.is_dir_cache(info[self.PARAM_MD5]):
                continue
            if not os.path.exists(cache):
                missing.append(info)
                continue
            collected.extend(self.load_dir_cache(md5))
        collected.extend(checksum_infos)
        return collected, missing

    def gc(self, checksum_infos):
        used_md5s = [info[self.PARAM_MD5] for info in self._collect(checksum_infos['local'])[0]]

        for md5 in self.all():
            if md5 in used_md5s:
                continue
            remove(self.get(md5))

    def status(self, checksum_infos, remote, jobs=1):
        checksum_infos = self._collect(checksum_infos)[0]
        md5s = [info[self.PARAM_MD5] for info in checksum_infos]
        path_infos = remote.md5s_to_path_infos(md5s)
        remote_exists = remote.exists(path_infos)
        local_exists = [not self.changed(md5) for md5 in md5s]

        return [(md5, STATUS_MAP[l,r]) for md5, l, r in zip(md5s, local_exists, remote_exists)]

    def _do_pull(self, checksum_infos, remote, jobs=1, no_progress_bar=False):
        md5s = [info[self.PARAM_MD5] for info in checksum_infos]

        # NOTE: filter files that are not corrupted
        md5s = list(filter(lambda md5: self.changed(md5), md5s))

        cache = [{'scheme': 'local', 'path': self.get(md5)} for md5 in md5s]
        path_infos = remote.md5s_to_path_infos(md5s)

        assert len(path_infos) == len(cache) == len(md5s)

        chunks = list(zip(to_chunks(path_infos, jobs),
                          to_chunks(cache, jobs),
                          to_chunks(md5s, jobs)))

        progress.set_n_total(len(md5s))

        if len(chunks) == 0:
            return

        futures = []
        with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
            for from_infos, to_infos, md5s in chunks:
                res = executor.submit(remote.download,
                                      from_infos,
                                      to_infos,
                                      names=md5s,
                                      no_progress_bar=no_progress_bar)
                futures.append(res)

        for f in futures:
            f.result()

    def pull(self, checksum_infos, remote, jobs=1):
        # NOTE: try fetching missing dir info
        checksum_infos, missing = self._collect(checksum_infos)
        if len(missing) > 0:
            self._do_pull(missing, remote, jobs, no_progress_bar=True)
            checksum_infos += self._collect(missing)[0]

        self._do_pull(checksum_infos, remote, jobs)

    def push(self, checksum_infos, remote, jobs=1):
        md5s = [info[self.PARAM_MD5] for info in self._collect(checksum_infos)[0]]

        # NOTE: verifying that our cache is not corrupted
        md5s = list(filter(lambda md5: not self.changed(md5), md5s))

        # NOTE: filter files that are already uploaded
        path_infos = remote.md5s_to_path_infos(md5s)
        md5s_exist = filter(lambda x: not x[1], list(zip(md5s, remote.exists(path_infos))))
        md5s = [md5 for md5, exists in md5s_exist]

        cache = [{'scheme': 'local', 'path': self.get(md5)} for md5 in md5s]
        path_infos = remote.md5s_to_path_infos(md5s)

        assert len(path_infos) == len(cache) == len(md5s)

        chunks = list(zip(to_chunks(path_infos, jobs),
                          to_chunks(cache, jobs),
                          to_chunks(md5s, jobs)))

        progress.set_n_total(len(md5s))

        if len(chunks) == 0:
            return

        futures = []
        with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
            for to_infos, from_infos, md5s in chunks:
                res = executor.submit(remote.upload, from_infos, to_infos, names=md5s)
                futures.append(res)

        for f in futures:
            f.result()
