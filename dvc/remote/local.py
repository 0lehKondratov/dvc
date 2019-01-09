import os
import stat
import uuid
import json
import ntpath
import shutil
import posixpath
from operator import itemgetter

import dvc.logger as logger
from dvc.system import System
from dvc.remote.base import RemoteBase, STATUS_MAP, STATUS_NEW, STATUS_DELETED
from dvc.utils import remove, move, copyfile, dict_md5, to_chunks
from dvc.utils import LARGE_DIR_SIZE
from dvc.config import Config
from dvc.exceptions import DvcException
from dvc.progress import progress
from concurrent.futures import ThreadPoolExecutor


class RemoteLOCAL(RemoteBase):
    scheme = 'local'
    REGEX = r'^(?P<path>.*)$'
    PARAM_CHECKSUM = 'md5'
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
        super(RemoteLOCAL, self).__init__(project, config)
        self.state = self.project.state if self.project else None
        self.protected = config.get(Config.SECTION_CACHE_PROTECTED, False)
        storagepath = config.get(Config.SECTION_AWS_STORAGEPATH, None)
        self.cache_dir = config.get(Config.SECTION_REMOTE_URL, storagepath)

        if self.cache_dir is not None and not os.path.isabs(self.cache_dir):
            cwd = config[Config.PRIVATE_CWD]
            self.cache_dir = os.path.abspath(os.path.join(cwd, self.cache_dir))

        types = config.get(Config.SECTION_CACHE_TYPE, None)
        if types:
            if isinstance(types, str):
                types = [t.strip() for t in types.split(',')]
            self.cache_types = types
        else:
            self.cache_types = self.CACHE_TYPES

        if self.cache_dir is not None and not os.path.exists(self.cache_dir):
            os.mkdir(self.cache_dir)

        self.path_info = {'scheme': 'local'}

    @staticmethod
    def compat_config(config):
        ret = config.copy()
        url = ret.pop(Config.SECTION_AWS_STORAGEPATH, '')
        ret[Config.SECTION_REMOTE_URL] = url
        return ret

    @property
    def url(self):
        return self.cache_dir

    @property
    def prefix(self):
        return self.cache_dir

    def list_cache_paths(self):
        clist = []
        for entry in os.listdir(self.cache_dir):
            subdir = os.path.join(self.cache_dir, entry)
            if not os.path.isdir(subdir):
                continue

            for cache in os.listdir(subdir):
                clist.append(os.path.join(subdir, cache))

        return clist

    def get(self, md5):
        if not md5:
            return None

        return os.path.join(self.cache_dir, md5[0:2], md5[2:])

    def changed_cache_file(self, md5):
        cache = self.get(md5)
        if self.state.changed(cache, md5=md5):
            if os.path.exists(cache):
                msg = 'Corrupted cache file {}.'
                logger.warning(msg.format(os.path.relpath(cache)))
                remove(cache)
            return True
        return False

    def exists(self, path_info):
        assert not isinstance(path_info, list)
        assert path_info['scheme'] == 'local'
        return os.path.exists(path_info['path'])

    def changed_cache(self, md5):
        cache = self.get(md5)
        clist = [(cache, md5)]

        while True:
            if len(clist) == 0:
                break

            cache, md5 = clist.pop()
            if self.changed_cache_file(md5):
                return True

            if not self.is_dir_cache(cache):
                continue

            for entry in self.load_dir_cache(md5):
                md5 = entry[self.PARAM_CHECKSUM]
                cache = self.get(md5)
                clist.append((cache, md5))

        return False

    def link(self, cache, path):
        assert os.path.isfile(cache)

        dname = os.path.dirname(path)
        if not os.path.exists(dname):
            os.makedirs(dname)

        # NOTE: just create an empty file for an empty cache
        if os.path.getsize(cache) == 0:
            open(path, 'w+').close()

            msg = "Created empty file: {} -> {}".format(cache, path)
            logger.debug(msg)
            return

        i = len(self.cache_types)
        while i > 0:
            try:
                self.CACHE_TYPE_MAP[self.cache_types[0]](cache, path)

                if self.protected:
                    os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)

                msg = "Created {}'{}': {} -> {}".format(
                    'protected ' if self.protected else '',
                    self.cache_types[0], cache, path)

                logger.debug(msg)
                return

            except DvcException as exc:
                msg = "Cache type '{}' is not supported: {}"
                logger.debug(msg.format(self.cache_types[0], str(exc)))
                del self.cache_types[0]
                i -= 1

        raise DvcException('no possible cache types left to try out.')

    @property
    def ospath(self):
        if os.name == 'nt':
            return ntpath
        return posixpath

    @classmethod
    def to_ospath(cls, path):
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

        for root, dirs, files in os.walk(dname):
            bar = False

            if len(files) > LARGE_DIR_SIZE:
                msg = "Computing md5 for a large directory {}. " \
                      "This is only done once."
                logger.info(msg.format(os.path.relpath(root)))
                bar = True
                title = os.path.relpath(root)
                processed = 0
                total = len(files)
                progress.update_target(title, 0, total)

            for fname in files:
                path = os.path.join(root, fname)
                relpath = self.unixpath(os.path.relpath(path, dname))

                if bar:
                    progress.update_target(title, processed, total)
                    processed += 1

                md5 = self.state.update(path)
                dir_info.append({self.PARAM_RELPATH: relpath,
                                 self.PARAM_CHECKSUM: md5})

            if bar:
                progress.finish_target(title)

        # NOTE: sorting the list by path to ensure reproducibility
        dir_info = sorted(dir_info, key=itemgetter(self.PARAM_RELPATH))

        md5 = dict_md5(dir_info) + self.MD5_DIR_SUFFIX
        if self.changed_cache_file(md5):
            self.dump_dir_cache(md5, dir_info)

        return (md5, dir_info)

    def load_dir_cache(self, md5):
        path = self.get(md5)

        assert self.is_dir_cache(path)

        try:
            with open(path, 'r') as fd:
                d = json.load(fd)
        except Exception:
            msg = u"Failed to load dir cache '{}'"
            logger.error(msg.format(os.path.relpath(path)))
            return []

        if not isinstance(d, list):
            msg = u"dir cache file format error '{}' [skipping the file]"
            logger.error(msg.format(os.path.relpath(path)))
            return []

        for info in d:
            info['relpath'] = self.to_ospath(info['relpath'])

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

    def do_checkout(self, path_info, checksum, force=False):
        path = path_info['path']
        md5 = checksum
        cache = self.get(md5)

        if not self.is_dir_cache(cache):
            if os.path.exists(path):
                self.safe_remove(path_info, force=force)

            self.link(cache, path)
            self.state.update_link(path)
            return

        # Create dir separately so that dir is created
        # even if there are no files in it
        if not os.path.exists(path):
            os.makedirs(path)

        dir_info = self.load_dir_cache(md5)
        dir_relpath = os.path.relpath(path)
        dir_size = len(dir_info)
        bar = dir_size > LARGE_DIR_SIZE

        logger.info("Linking directory '{}'.".format(dir_relpath))

        for processed, entry in enumerate(dir_info):
            relpath = entry[self.PARAM_RELPATH]
            m = entry[self.PARAM_CHECKSUM]
            p = os.path.join(path, relpath)
            c = self.get(m)

            entry_info = {'scheme': path_info['scheme'], self.PARAM_PATH: p}

            entry_checksum_info = {self.PARAM_CHECKSUM: m}

            if self.changed(entry_info, entry_checksum_info):
                if os.path.exists(p):
                    self.safe_remove(entry_info, force=force)

                self.link(c, p)

            if bar:
                progress.update_target(dir_relpath, processed, dir_size)

        self._discard_working_directory_changes(path, dir_info, force=force)

        self.state.update_link(path)

        if bar:
            progress.finish_target(dir_relpath)

    def already_cached(self, path_info):
        assert path_info['scheme'] in ['', 'local']

        current_md5 = self.state.update(path_info['path'])

        if not current_md5:
            return False

        return not self.changed_cache(current_md5)

    def _discard_working_directory_changes(self, path, dir_info, force=False):
        working_dir_files = set(
            os.path.join(root, file)
            for root, _, files in os.walk(path)
            for file in files
        )

        cached_files = set(
            os.path.join(path, file['relpath'])
            for file in dir_info
        )

        delta = working_dir_files - cached_files

        for file in delta:
            self.safe_remove({'scheme': 'local', 'path': file}, force=force)

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
        self.state.update_link(path)

        return {self.PARAM_CHECKSUM: md5}

    def _save_dir(self, path_info):
        path = path_info['path']
        md5, dir_info = self.state.update_info(path)
        dir_relpath = os.path.relpath(path)
        dir_size = len(dir_info)
        bar = dir_size > LARGE_DIR_SIZE

        logger.info("Linking directory '{}'.".format(dir_relpath))

        for processed, entry in enumerate(dir_info):
            relpath = entry[self.PARAM_RELPATH]
            m = entry[self.PARAM_CHECKSUM]
            p = os.path.join(path, relpath)
            c = self.get(m)

            if self.changed_cache(m):
                self._move(p, c)
            else:
                remove(p)

            self.link(c, p)

            if bar:
                progress.update_target(dir_relpath, processed, dir_size)

        self.state.update_link(path)

        if bar:
            progress.finish_target(dir_relpath)

        return {self.PARAM_CHECKSUM: md5}

    def save(self, path_info):
        if path_info['scheme'] != 'local':
            raise NotImplementedError

        path = path_info['path']

        msg = "Saving '{}' to cache '{}'."
        logger.info(msg.format(os.path.relpath(path),
                               os.path.relpath(self.cache_dir)))

        if os.path.isdir(path):
            return self._save_dir(path_info)
        else:
            return self._save_file(path_info)

    def save_info(self, path_info):
        if path_info['scheme'] != 'local':
            raise NotImplementedError

        return {self.PARAM_CHECKSUM: self.state.update(path_info['path'])}

    def remove(self, path_info):
        if path_info['scheme'] != 'local':
            raise NotImplementedError

        remove(path_info['path'])

    def move(self, from_info, to_info):
        if from_info['scheme'] != 'local' or to_info['scheme'] != 'local':
            raise NotImplementedError

        move(from_info['path'], to_info['path'])

    def cache_exists(self, md5s):
        assert isinstance(md5s, list)
        return list(filter(lambda md5: not self.changed_cache_file(md5), md5s))

    def upload(self, from_infos, to_infos, names=None):
        names = self._verify_path_args(to_infos, from_infos, names)

        for from_info, to_info, name in zip(from_infos, to_infos, names):
            if to_info['scheme'] != 'local':
                raise NotImplementedError

            if from_info['scheme'] != 'local':
                raise NotImplementedError

            logger.debug("Uploading '{}' to '{}'".format(from_info['path'],
                                                         to_info['path']))

            if not name:
                name = os.path.basename(from_info['path'])

            self._makedirs(to_info['path'])

            try:
                copyfile(from_info['path'], to_info['path'], name=name)
            except Exception:
                logger.error(
                    "failed to upload '{}' to '{}'"
                    .format(from_info['path'], to_info['path'])
                )

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

            logger.debug("Downloading '{}' to '{}'".format(from_info['path'],
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
            except Exception:
                logger.error(
                    "failed to download '{}' to '{}'"
                    .format(from_info['path'], to_info['path'])
                )

                continue

            os.rename(tmp_file, to_info['path'])

    def _group(self, checksum_infos, show_checksums=False):
        by_md5 = {}

        for info in checksum_infos:
            md5 = info[self.PARAM_CHECKSUM]

            if show_checksums:
                by_md5[md5] = {'name': md5}
                continue

            name = info[self.PARAM_PATH]
            branch = info.get('branch')
            if branch:
                name += '({})'.format(branch)

            if md5 not in by_md5.keys():
                by_md5[md5] = {'name': name}
            else:
                by_md5[md5]['name'] += ' ' + name

        return by_md5

    def status(self, checksum_infos, remote, jobs=None, show_checksums=False):
        logger.info("Preparing to collect status from {}".format(remote.url))
        title = "Collecting information"

        ret = {}

        progress.set_n_total(1)
        progress.update_target(title, 0, 100)

        progress.update_target(title, 10, 100)

        ret = self._group(checksum_infos, show_checksums=show_checksums)
        md5s = list(ret.keys())

        progress.update_target(title, 30, 100)

        remote_exists = list(remote.cache_exists(md5s))

        progress.update_target(title, 90, 100)

        local_exists = self.cache_exists(md5s)

        progress.finish_target(title)

        for md5, info in ret.items():
            info['status'] = STATUS_MAP[(md5 in local_exists,
                                         md5 in remote_exists)]

        return ret

    def _get_chunks(self, download, remote, status_info, status, jobs):
        title = "Analysing status."

        progress.set_n_total(1)
        total = len(status_info)
        current = 0

        cache = []
        path_infos = []
        names = []
        for md5, info in status_info.items():
            if info['status'] == status:
                cache.append(self.checksum_to_path_info(md5))
                path_infos.append(remote.checksum_to_path_info(md5))
                names.append(info['name'])
            current += 1
            progress.update_target(title, current, total)

        progress.finish_target(title)

        progress.set_n_total(len(names))

        if download:
            to_infos = cache
            from_infos = path_infos
        else:
            to_infos = path_infos
            from_infos = cache

        return list(zip(to_chunks(from_infos, jobs),
                        to_chunks(to_infos, jobs),
                        to_chunks(names, jobs)))

    def _process(self,
                 checksum_infos,
                 remote,
                 jobs=None,
                 show_checksums=False,
                 download=False):
        msg = "Preparing to {} data {} '{}'"
        logger.info(msg.format('download' if download else 'upload',
                               'from' if download else 'to',
                               remote.url))

        if download:
            func = remote.download
            status = STATUS_DELETED
        else:
            func = remote.upload
            status = STATUS_NEW

        if jobs is None:
            jobs = remote.JOBS

        status_info = self.status(checksum_infos,
                                  remote,
                                  jobs=jobs,
                                  show_checksums=show_checksums)

        chunks = self._get_chunks(download, remote, status_info, status, jobs)

        if len(chunks) == 0:
            return

        futures = []
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            for from_infos, to_infos, names in chunks:
                res = executor.submit(func,
                                      from_infos,
                                      to_infos,
                                      names=names)
                futures.append(res)

        for f in futures:
            f.result()

    def push(self,
             checksum_infos,
             remote,
             jobs=None,
             show_checksums=False):
        self._process(checksum_infos,
                      remote,
                      jobs=jobs,
                      show_checksums=show_checksums,
                      download=False)

    def pull(self,
             checksum_infos,
             remote,
             jobs=None,
             show_checksums=False):
        self._process(checksum_infos,
                      remote,
                      jobs=jobs,
                      show_checksums=show_checksums,
                      download=True)
