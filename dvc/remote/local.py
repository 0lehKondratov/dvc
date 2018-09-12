import os
import uuid
import json
import ntpath
import shutil
import posixpath
from operator import itemgetter

from dvc.system import System
from dvc.remote.base import RemoteBase, STATUS_MAP
from dvc.logger import Logger
from dvc.utils import remove, move, copyfile, dict_md5, to_chunks
from dvc.utils import LARGE_DIR_SIZE
from dvc.config import Config
from dvc.exceptions import DvcException
from dvc.progress import progress
from concurrent.futures import ThreadPoolExecutor


class RemoteLOCAL(RemoteBase):
    scheme = ''
    REGEX = r'^(?P<path>(/+|.:\\+).*)$'
    PARAM_MD5 = 'md5'
    PARAM_PATH = 'path'
    PARAM_RELPATH = 'relpath'
    MD5_DIR_SUFFIX = '.dir'

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

        if self.cache_dir is not None and not os.path.exists(self.cache_dir):
            os.mkdir(self.cache_dir)

    @property
    def url(self):
        return self.cache_dir

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

    def changed_cache(self, md5):
        cache = self.get(md5)
        if self.state.changed(cache, md5=md5):
            if os.path.exists(cache):
                msg = 'Corrupted cache file {}'
                Logger.warn(msg.format(os.path.relpath(cache)))
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
                msg = "Created '{}': {} -> {}"
                Logger.info(msg.format(self.cache_types[0],
                                       os.path.relpath(cache),
                                       os.path.relpath(path)))
                return
            except Exception as exc:
                msg = 'Cache type \'{}\' is not supported: {}'
                Logger.debug(msg.format(self.cache_types[0], str(exc)))
                del self.cache_types[0]
                i -= 1

        raise DvcException('No possible cache types left to try out.')

    @classmethod
    def ospath(cls, path):
        if os.name == 'nt':
            return cls.ntpath(path)
        return cls.unixpath(path)

    @staticmethod
    def unixpath(path):
        assert not ntpath.isabs(path)
        assert not posixpath.isabs(path)
        return path.replace('\\', '/')

    @staticmethod
    def ntpath(path):
        assert not ntpath.isabs(path)
        assert not posixpath.isabs(path)
        return path.replace('/', '\\')

    def collect_dir_cache(self, dname):
        dir_info = []

        db = self.state.load()
        bar = False
        for root, dirs, files in os.walk(dname):
            if len(files) > LARGE_DIR_SIZE:
                msg = "Computing md5 for a large directory {}. " \
                      "This is only done once."
                Logger.info(msg.format(os.path.relpath(dname)))
                bar = True
                title = os.path.relpath(dname)
                processed = 0
                total = len(files)
                progress.update_target(title, 0, total)

            for fname in files:
                path = os.path.join(root, fname)
                relpath = self.unixpath(os.path.relpath(path, dname))

                if bar:
                    progress.update_target(title, processed, total)
                    processed += 1

                md5 = self.state.update(path, use_db=db)
                dir_info.append({self.PARAM_RELPATH: relpath,
                                 self.PARAM_MD5: md5})

        db.commit()
        db.close()

        if bar:
            progress.finish_target(title)

        # NOTE: sorting the list by path to ensure reproducibility
        dir_info = sorted(dir_info, key=itemgetter(self.PARAM_RELPATH))

        md5 = dict_md5(dir_info) + self.MD5_DIR_SUFFIX
        if self.changed_cache(md5):
            self.dump_dir_cache(md5, dir_info)

        return (md5, dir_info)

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

        for info in d:
            info['relpath'] = self.ospath(info['relpath'])

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

    @classmethod
    def is_dir_cache(cls, cache):
        return cache.endswith(cls.MD5_DIR_SUFFIX)

    def checkout(self, path_info, checksum_info):
        path = path_info['path']
        md5 = checksum_info.get(self.PARAM_MD5, None)
        cache = self.get(md5)

        if not cache:
            msg = 'No cache info for \'{}\'. Skipping checkout.'
            Logger.warn(msg.format(os.path.relpath(path)))
            return

        if not self.changed(path_info, checksum_info):
            msg = "Data '{}' didn't change."
            Logger.info(msg.format(os.path.relpath(path)))
            return

        if self.changed_cache(md5):
            msg = u'Cache \'{}\' not found. File \'{}\' won\'t be created.'
            Logger.warn(msg.format(md5, os.path.relpath(path)))
            remove(path)
            return

        if os.path.exists(path):
            msg = u'Data \'{}\' exists. Removing before checkout.'
            Logger.warn(msg.format(os.path.relpath(path)))
            remove(path)

        msg = u'Checking out \'{}\' with cache \'{}\'.'
        Logger.info(msg.format(os.path.relpath(path), md5))

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
        assert md5 is not None

        cache = self.get(md5)

        if self.changed_cache(md5):
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
            relpath = entry[self.PARAM_RELPATH]
            m = entry[self.PARAM_MD5]
            p = os.path.join(path, relpath)
            c = self.get(m)

            if self.changed_cache(m):
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

        msg = "Saving '{}' to cache '{}'."
        Logger.info(msg.format(os.path.relpath(path),
                               os.path.relpath(self.cache_dir)))

        if os.path.isdir(path):
            return self._save_dir(path_info)
        else:
            return self._save_file(path_info)

    def save_info(self, path_info):
        if path_info['scheme'] != 'local':
            raise NotImplementedError

        return {self.PARAM_MD5: self.state.update(path_info['path'])}

    def changed(self, path_info, checksum_info):
        if not self.exists([path_info])[0]:
            return True

        md5 = checksum_info.get(self.PARAM_MD5, None)
        if md5 is None:
            return True

        if self.changed_cache(md5):
            return True

        return checksum_info != self.save_info(path_info)

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
                 'path': os.path.join(self.prefix,
                                      md5[0:2], md5[2:])} for md5 in md5s]

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

            Logger.debug("Uploading '{}' to '{}'".format(from_info['path'],
                                                         to_info['path']))

            if not name:
                name = os.path.basename(from_info['path'])

            self._makedirs(to_info['path'])

            try:
                copyfile(from_info['path'], to_info['path'], name=name)
            except Exception as exc:
                msg = "Failed to upload '{}' tp '{}'"
                Logger.error(msg.format(from_info['path'],
                                        to_info['path']), exc)

    def download(self,
                 from_infos,
                 to_infos,
                 no_progress_bar=False,
                 names=None):
        names = self._verify_path_args(from_infos, to_infos, names)

        for to_info, from_info, name in zip(to_infos, from_infos, names):
            if from_info['scheme'] != 'local':
                raise NotImplementedError

            if to_info['scheme'] != 'local':
                raise NotImplementedError

            Logger.debug("Downloading '{}' to '{}'".format(from_info['path'],
                                                           to_info['path']))

            if not name:
                name = os.path.basename(to_info['path'])

            self._makedirs(to_info['path'])
            tmp_file = self.tmp_file(to_info['path'])
            try:
                copyfile(from_info['path'],
                         tmp_file,
                         no_progress_bar=no_progress_bar,
                         name=name)
            except Exception as exc:
                msg = "Failed to download '{}' to '{}'"
                Logger.error(msg.format(from_info['path'],
                                        to_info['path']), exc)
                continue

            os.rename(tmp_file, to_info['path'])

    def _collect(self, checksum_infos, push=False):
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

            for i in self.load_dir_cache(md5):
                if info.get('branch'):
                    i['branch'] = info['branch']
                i[self.PARAM_PATH] = posixpath.join(info[self.PARAM_PATH],
                                                    i[self.PARAM_RELPATH])
                collected.append(i.copy())

        collected.extend(checksum_infos)
        return collected, missing

    def _group(self, checksum_infos, show_checksums=False):
        by_md5 = {}

        for info in checksum_infos:
            md5 = info[self.PARAM_MD5]

            if show_checksums:
                by_md5[md5] = md5
                continue

            name = info[self.PARAM_PATH]
            branch = info.get('branch')
            if branch:
                name += '({})'.format(branch)

            if md5 not in by_md5.keys():
                by_md5[md5] = ''
            else:
                by_md5[md5] += ' '

            by_md5[md5] += name

        return list(by_md5.keys()), list(by_md5.values())

    def gc(self, checksum_infos):
        checksum_infos = self._collect(checksum_infos['local'])[0]
        used_md5s = [info[self.PARAM_MD5] for info in checksum_infos]

        removed = False
        for md5 in self.all():
            if md5 in used_md5s:
                continue
            remove(self.get(md5))
            removed = True

        return removed

    def status(self, checksum_infos, remote, jobs=1, show_checksums=False):
        Logger.info("Preparing to pull data from {}".format(remote.url))
        title = "Collecting information"

        progress.set_n_total(0)
        progress.update_target(title, 0, 100)

        checksum_infos, missing = self._collect(checksum_infos)
        checksum_infos += missing

        progress.update_target(title, 10, 100)

        md5s, names = self._group(checksum_infos,
                                  show_checksums=show_checksums)

        progress.update_target(title, 20, 100)

        path_infos = remote.md5s_to_path_infos(md5s)

        progress.update_target(title, 30, 100)

        remote_exists = remote.exists(path_infos)

        progress.update_target(title, 90, 100)

        local_exists = [not self.changed_cache(md5) for md5 in md5s]

        progress.finish_target(title)

        return [(name, STATUS_MAP[l, r]) for name, l, r in zip(names,
                                                               local_exists,
                                                               remote_exists)]

    def _do_pull(self,
                 checksum_infos,
                 remote,
                 jobs=1,
                 show_checksums=False):
        title = "Collecting information"

        progress.set_n_total(0)
        progress.update_target(title, 0, 100)

        grouped = zip(*self._group(checksum_infos,
                                   show_checksums=show_checksums))

        progress.update_target(title, 10, 100)

        md5s = []
        names = []
        # NOTE: filter files that are not corrupted
        for md5, name in grouped:
            if self.changed_cache(md5):
                md5s.append(md5)
                names.append(name)

        progress.update_target(title, 30, 100)

        cache = [{'scheme': 'local', 'path': self.get(md5)} for md5 in md5s]

        progress.update_target(title, 50, 100)

        path_infos = remote.md5s_to_path_infos(md5s)

        progress.update_target(title, 60, 100)

        assert len(path_infos) == len(cache) == len(md5s) == len(names)

        chunks = list(zip(to_chunks(path_infos, jobs),
                          to_chunks(cache, jobs),
                          to_chunks(names, jobs)))

        progress.finish_target(title)

        progress.set_n_total(len(names))

        if len(chunks) == 0:
            return

        futures = []
        with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
            for from_infos, to_infos, names in chunks:
                res = executor.submit(remote.download,
                                      from_infos,
                                      to_infos,
                                      names=names)
                futures.append(res)

        for f in futures:
            f.result()

    def pull(self, checksum_infos, remote, jobs=1, show_checksums=False):
        Logger.info("Preparing to pull data from {}".format(remote.url))

        # NOTE: try fetching missing dir info
        checksum_infos, missing = self._collect(checksum_infos)
        if len(missing) > 0:
            self._do_pull(missing,
                          remote,
                          jobs,
                          show_checksums=show_checksums)
            checksum_infos += self._collect(missing)[0]

        self._do_pull(checksum_infos,
                      remote,
                      jobs,
                      show_checksums=show_checksums)

    def push(self, checksum_infos, remote, jobs=1, show_checksums=False):
        Logger.info("Preparing to push data to {}".format(remote.url))
        title = "Collecting information"

        progress.set_n_total(0)
        progress.update_target(title, 0, 100)

        checksum_infos = self._collect(checksum_infos)[0]

        progress.update_target(title, 10, 100)

        # NOTE: verifying that our cache is not corrupted
        def func(info):
            return not self.changed_cache(info[self.PARAM_MD5])
        checksum_infos = list(filter(func, checksum_infos))

        progress.update_target(title, 20, 100)

        # NOTE: filter files that are already uploaded
        md5s = [i[self.PARAM_MD5] for i in checksum_infos]
        exists = remote.exists(remote.md5s_to_path_infos(md5s))

        progress.update_target(title, 30, 100)

        def func(entry):
            return not entry[0]

        assert len(exists) == len(checksum_infos)
        infos_exist = list(filter(func, zip(exists, checksum_infos)))
        checksum_infos = [i for e, i in infos_exist]

        progress.update_target(title, 70, 100)

        md5s, names = self._group(checksum_infos,
                                  show_checksums=show_checksums)
        cache = [{'scheme': 'local', 'path': self.get(md5)} for md5 in md5s]

        progress.update_target(title, 80, 100)

        path_infos = remote.md5s_to_path_infos(md5s)

        assert len(path_infos) == len(cache) == len(md5s) == len(names)

        progress.update_target(title, 90, 100)

        chunks = list(zip(to_chunks(path_infos, jobs),
                          to_chunks(cache, jobs),
                          to_chunks(names, jobs)))

        progress.finish_target(title)

        progress.set_n_total(len(names))

        if len(chunks) == 0:
            return

        futures = []
        with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
            for to_infos, from_infos, names in chunks:
                res = executor.submit(remote.upload,
                                      from_infos,
                                      to_infos,
                                      names=names)
                futures.append(res)

        for f in futures:
            f.result()
