import errno
import logging
import os
import stat
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial

from funcy import cached_property, concat
from shortuuid import uuid

from dvc.exceptions import DownloadError, DvcException, UploadError
from dvc.path_info import PathInfo
from dvc.progress import Tqdm
from dvc.remote.base import (
    STATUS_DELETED,
    STATUS_MAP,
    STATUS_MISSING,
    STATUS_NEW,
    BaseRemote,
    BaseRemoteTree,
    index_locked,
)
from dvc.remote.index import RemoteIndexNoop
from dvc.scheme import Schemes
from dvc.scm.tree import WorkingTree, is_working_tree
from dvc.system import System
from dvc.utils import file_md5, relpath, tmp_fname
from dvc.utils.fs import (
    copy_fobj_to_file,
    copyfile,
    makedirs,
    move,
    remove,
    walk_files,
)

logger = logging.getLogger(__name__)


class LocalRemoteTree(BaseRemoteTree):
    SHARED_MODE_MAP = {None: (0o644, 0o755), "group": (0o664, 0o775)}
    PATH_CLS = PathInfo

    def __init__(self, remote, config):
        super().__init__(remote, config)
        self.path_info = config.get("url")

    @property
    def repo(self):
        return self.remote.repo

    @cached_property
    def _work_tree(self):
        if self.repo:
            return WorkingTree(self.repo.root_dir)
        return None

    @property
    def work_tree(self):
        # When using repo.brancher, repo.tree may change to/from WorkingTree to
        # GitTree arbitarily. When repo.tree is GitTree, local cache needs to
        # use its own WorkingTree instance.
        if self.repo and not is_working_tree(self.repo.tree):
            return self._work_tree
        return None

    @staticmethod
    def open(path_info, mode="r", encoding=None):
        return open(path_info, mode=mode, encoding=encoding)

    def exists(self, path_info):
        assert isinstance(path_info, str) or path_info.scheme == "local"
        if not self.repo:
            return os.path.exists(path_info)
        if self.work_tree and self.work_tree.exists(path_info):
            return True
        return self.repo.tree.exists(path_info)

    def isfile(self, path_info):
        if not self.repo:
            return os.path.isfile(path_info)
        if self.work_tree and self.work_tree.isfile(path_info):
            return True
        return self.repo.tree.isfile(path_info)

    def isdir(self, path_info):
        if not self.repo:
            return os.path.isdir(path_info)
        if self.work_tree and self.work_tree.isdir(path_info):
            return True
        return self.repo.tree.isdir(path_info)

    def iscopy(self, path_info):
        return not (
            System.is_symlink(path_info) or System.is_hardlink(path_info)
        )

    def walk_files(self, path_info, **kwargs):
        if self.work_tree:
            tree = self.work_tree
        else:
            tree = self.repo.tree
        for fname in tree.walk_files(path_info):
            yield PathInfo(fname)

    def is_empty(self, path_info):
        path = path_info.fspath

        if self.isfile(path_info) and os.path.getsize(path) == 0:
            return True

        if self.isdir(path_info) and len(os.listdir(path)) == 0:
            return True

        return False

    def remove(self, path_info):
        if isinstance(path_info, PathInfo):
            if path_info.scheme != "local":
                raise NotImplementedError
            path = path_info.fspath
        else:
            path = path_info

        if self.exists(path):
            remove(path)

    def makedirs(self, path_info):
        makedirs(path_info, exist_ok=True, mode=self.dir_mode)

    def move(self, from_info, to_info, mode=None):
        if from_info.scheme != "local" or to_info.scheme != "local":
            raise NotImplementedError

        self.makedirs(to_info.parent)

        if mode is None:
            if self.isfile(from_info):
                mode = self.file_mode
            else:
                mode = self.dir_mode

        move(from_info, to_info, mode=mode)

    def copy(self, from_info, to_info):
        tmp_info = to_info.parent / tmp_fname(to_info.name)
        try:
            System.copy(from_info, tmp_info)
            os.chmod(tmp_info, self.file_mode)
            os.rename(tmp_info, to_info)
        except Exception:
            self.remove(tmp_info)
            raise

    def copy_fobj(self, fobj, to_info):
        self.makedirs(to_info.parent)
        tmp_info = to_info.parent / tmp_fname(to_info.name)
        try:
            copy_fobj_to_file(fobj, tmp_info)
            os.chmod(tmp_info, self.file_mode)
            os.rename(tmp_info, to_info)
        except Exception:
            self.remove(tmp_info)
            raise

    @staticmethod
    def symlink(from_info, to_info):
        System.symlink(from_info, to_info)

    @staticmethod
    def is_symlink(path_info):
        return System.is_symlink(path_info)

    def hardlink(self, from_info, to_info):
        # If there are a lot of empty files (which happens a lot in datasets),
        # and the cache type is `hardlink`, we might reach link limits and
        # will get something like: `too many links error`
        #
        # This is because all those empty files will have the same checksum
        # (i.e. 68b329da9893e34099c7d8ad5cb9c940), therefore, they will be
        # linked to the same file in the cache.
        #
        # From https://en.wikipedia.org/wiki/Hard_link
        #   * ext4 limits the number of hard links on a file to 65,000
        #   * Windows with NTFS has a limit of 1024 hard links on a file
        #
        # That's why we simply create an empty file rather than a link.
        if self.getsize(from_info) == 0:
            self.open(to_info, "w").close()

            logger.debug(
                "Created empty file: {src} -> {dest}".format(
                    src=str(from_info), dest=str(to_info)
                )
            )
            return

        System.hardlink(from_info, to_info)

    @staticmethod
    def is_hardlink(path_info):
        return System.is_hardlink(path_info)

    def reflink(self, from_info, to_info):
        tmp_info = to_info.parent / tmp_fname(to_info.name)
        System.reflink(from_info, tmp_info)
        # NOTE: reflink has its own separate inode, so you can set permissions
        # that are different from the source.
        os.chmod(tmp_info, self.file_mode)
        os.rename(tmp_info, to_info)

    @staticmethod
    def getsize(path_info):
        return os.path.getsize(path_info)

    def _upload(
        self, from_file, to_info, name=None, no_progress_bar=False, **_kwargs
    ):
        makedirs(to_info.parent, exist_ok=True)

        tmp_file = tmp_fname(to_info)
        copyfile(
            from_file, tmp_file, name=name, no_progress_bar=no_progress_bar
        )

        self.remote.protect(tmp_file)
        os.rename(tmp_file, to_info)

    @staticmethod
    def _download(
        from_info, to_file, name=None, no_progress_bar=False, **_kwargs
    ):
        copyfile(
            from_info, to_file, no_progress_bar=no_progress_bar, name=name
        )


class LocalRemote(BaseRemote):
    scheme = Schemes.LOCAL
    PARAM_CHECKSUM = "md5"
    PARAM_PATH = "path"
    TRAVERSE_PREFIX_LEN = 2
    INDEX_CLS = RemoteIndexNoop
    TREE_CLS = LocalRemoteTree

    UNPACKED_DIR_SUFFIX = ".unpacked"

    DEFAULT_CACHE_TYPES = ["reflink", "copy"]

    CACHE_MODE = 0o444

    def __init__(self, repo, config):
        super().__init__(repo, config)
        self.cache_dir = config.get("url")

    @property
    def state(self):
        return self.repo.state

    @property
    def cache_dir(self):
        return self.tree.path_info.fspath if self.tree.path_info else None

    @cache_dir.setter
    def cache_dir(self, value):
        self.tree.path_info = PathInfo(value) if value else None

    @classmethod
    def supported(cls, config):
        return True

    @cached_property
    def cache_path(self):
        return os.path.abspath(self.cache_dir)

    def checksum_to_path(self, checksum):
        # NOTE: `self.cache_path` is already normalized so we can simply use
        # `os.sep` instead of `os.path.join`. This results in this helper
        # being ~5.5 times faster.
        return (
            f"{self.cache_path}{os.sep}{checksum[0:2]}{os.sep}{checksum[2:]}"
        )

    def list_cache_paths(self, prefix=None, progress_callback=None):
        assert self.path_info is not None
        if prefix:
            path_info = self.path_info / prefix[:2]
            if not self.tree.exists(path_info):
                return
        else:
            path_info = self.path_info
        if progress_callback:
            for path in walk_files(path_info):
                progress_callback()
                yield path
        else:
            yield from walk_files(path_info)

    def get(self, md5):
        if not md5:
            return None

        return self.checksum_to_path_info(md5).url

    def already_cached(self, path_info):
        assert path_info.scheme in ["", "local"]

        current_md5 = self.get_checksum(path_info)

        if not current_md5:
            return False

        return not self.changed_cache(current_md5)

    def _verify_link(self, path_info, link_type):
        if link_type == "hardlink" and self.tree.getsize(path_info) == 0:
            return

        super()._verify_link(path_info, link_type)

    def get_file_checksum(self, path_info):
        return file_md5(path_info)[0]

    def cache_exists(self, checksums, jobs=None, name=None):
        return [
            checksum
            for checksum in Tqdm(
                checksums,
                unit="file",
                desc="Querying "
                + ("cache in " + name if name else "local cache"),
            )
            if not self.changed_cache_file(checksum)
        ]

    @index_locked
    def status(
        self,
        named_cache,
        remote,
        jobs=None,
        show_checksums=False,
        download=False,
    ):
        # Return flattened dict containing all status info
        dir_status, file_status, _ = self._status(
            named_cache,
            remote,
            jobs=jobs,
            show_checksums=show_checksums,
            download=download,
        )
        return dict(dir_status, **file_status)

    def _status(
        self,
        named_cache,
        remote,
        jobs=None,
        show_checksums=False,
        download=False,
    ):
        """Return a tuple of (dir_status_info, file_status_info, dir_contents).

        dir_status_info contains status for .dir files, file_status_info
        contains status for all other files, and dir_contents is a dict of
        {dir_checksum: set(file_checksum, ...)} which can be used to map
        a .dir file to its file contents.
        """
        logger.debug(f"Preparing to collect status from {remote.path_info}")
        md5s = set(named_cache.scheme_keys(self.scheme))

        logger.debug("Collecting information from local cache...")
        local_exists = frozenset(
            self.cache_exists(md5s, jobs=jobs, name=self.cache_dir)
        )

        # This is a performance optimization. We can safely assume that,
        # if the resources that we want to fetch are already cached,
        # there's no need to check the remote storage for the existence of
        # those files.
        if download and local_exists == md5s:
            remote_exists = local_exists
        else:
            logger.debug("Collecting information from remote cache...")
            remote_exists = set()
            dir_md5s = set(named_cache.dir_keys(self.scheme))
            if dir_md5s:
                remote_exists.update(
                    self._indexed_dir_checksums(named_cache, remote, dir_md5s)
                )
                md5s.difference_update(remote_exists)
            if md5s:
                remote_exists.update(
                    remote.cache_exists(
                        md5s, jobs=jobs, name=str(remote.path_info)
                    )
                )
        return self._make_status(
            named_cache, show_checksums, local_exists, remote_exists
        )

    def _make_status(
        self, named_cache, show_checksums, local_exists, remote_exists
    ):
        def make_names(checksum, names):
            return {"name": checksum if show_checksums else " ".join(names)}

        dir_status = {}
        file_status = {}
        dir_contents = {}
        for checksum, item in named_cache[self.scheme].items():
            if item.children:
                dir_status[checksum] = make_names(checksum, item.names)
                dir_contents[checksum] = set()
                for child_checksum, child in item.children.items():
                    file_status[child_checksum] = make_names(
                        child_checksum, child.names
                    )
                    dir_contents[checksum].add(child_checksum)
            else:
                file_status[checksum] = make_names(checksum, item.names)

        self._fill_statuses(dir_status, local_exists, remote_exists)
        self._fill_statuses(file_status, local_exists, remote_exists)

        self._log_missing_caches(dict(dir_status, **file_status))

        return dir_status, file_status, dir_contents

    def _indexed_dir_checksums(self, named_cache, remote, dir_md5s):
        # Validate our index by verifying all indexed .dir checksums
        # still exist on the remote
        indexed_dirs = set(remote.index.dir_checksums())
        indexed_dir_exists = set()
        if indexed_dirs:
            indexed_dir_exists.update(
                remote._cache_object_exists(indexed_dirs)
            )
            missing_dirs = indexed_dirs.difference(indexed_dir_exists)
            if missing_dirs:
                logger.debug(
                    "Remote cache missing indexed .dir checksums '{}', "
                    "clearing remote index".format(", ".join(missing_dirs))
                )
                remote.index.clear()

        # Check if non-indexed (new) dir checksums exist on remote
        dir_exists = dir_md5s.intersection(indexed_dir_exists)
        dir_exists.update(remote._cache_object_exists(dir_md5s - dir_exists))

        # If .dir checksum exists on the remote, assume directory contents
        # still exists on the remote
        for dir_checksum in dir_exists:
            file_checksums = list(
                named_cache.child_keys(self.scheme, dir_checksum)
            )
            if dir_checksum not in remote.index:
                logger.debug(
                    "Indexing new .dir '{}' with '{}' nested files".format(
                        dir_checksum, len(file_checksums)
                    )
                )
                remote.index.update([dir_checksum], file_checksums)
            yield dir_checksum
            yield from file_checksums

    @staticmethod
    def _fill_statuses(checksum_info_dir, local_exists, remote_exists):
        # Using sets because they are way faster for lookups
        local = set(local_exists)
        remote = set(remote_exists)

        for md5, info in checksum_info_dir.items():
            status = STATUS_MAP[(md5 in local, md5 in remote)]
            info["status"] = status

    def _get_plans(self, download, remote, status_info, status):
        cache = []
        path_infos = []
        names = []
        checksums = []
        for md5, info in Tqdm(
            status_info.items(), desc="Analysing status", unit="file"
        ):
            if info["status"] == status:
                cache.append(self.checksum_to_path_info(md5))
                path_infos.append(remote.checksum_to_path_info(md5))
                names.append(info["name"])
                checksums.append(md5)

        if download:
            to_infos = cache
            from_infos = path_infos
        else:
            to_infos = path_infos
            from_infos = cache

        return from_infos, to_infos, names, checksums

    def _process(
        self,
        named_cache,
        remote,
        jobs=None,
        show_checksums=False,
        download=False,
    ):
        logger.debug(
            "Preparing to {} '{}'".format(
                "download data from" if download else "upload data to",
                remote.path_info,
            )
        )

        if download:
            func = partial(
                remote.tree.download,
                dir_mode=self.tree.dir_mode,
                file_mode=self.tree.file_mode,
            )
            status = STATUS_DELETED
            desc = "Downloading"
        else:
            func = remote.tree.upload
            status = STATUS_NEW
            desc = "Uploading"

        if jobs is None:
            jobs = remote.JOBS

        dir_status, file_status, dir_contents = self._status(
            named_cache,
            remote,
            jobs=jobs,
            show_checksums=show_checksums,
            download=download,
        )

        dir_plans = self._get_plans(download, remote, dir_status, status)
        file_plans = self._get_plans(download, remote, file_status, status)

        total = len(dir_plans[0]) + len(file_plans[0])
        if total == 0:
            return 0

        with Tqdm(total=total, unit="file", desc=desc) as pbar:
            func = pbar.wrap_fn(func)
            with ThreadPoolExecutor(max_workers=jobs) as executor:
                if download:
                    fails = sum(executor.map(func, *dir_plans))
                    fails += sum(executor.map(func, *file_plans))
                else:
                    # for uploads, push files first, and any .dir files last

                    file_futures = {}
                    for from_info, to_info, name, checksum in zip(*file_plans):
                        file_futures[checksum] = executor.submit(
                            func, from_info, to_info, name
                        )
                    dir_futures = {}
                    for from_info, to_info, name, dir_checksum in zip(
                        *dir_plans
                    ):
                        wait_futures = {
                            future
                            for file_checksum, future in file_futures.items()
                            if file_checksum in dir_contents[dir_checksum]
                        }
                        dir_futures[dir_checksum] = executor.submit(
                            self._dir_upload,
                            func,
                            wait_futures,
                            from_info,
                            to_info,
                            name,
                        )
                    fails = sum(
                        future.result()
                        for future in concat(
                            file_futures.values(), dir_futures.values()
                        )
                    )

        if fails:
            if download:
                remote.index.clear()
                raise DownloadError(fails)
            raise UploadError(fails)

        if not download:
            # index successfully pushed dirs
            for dir_checksum, future in dir_futures.items():
                if future.result() == 0:
                    file_checksums = dir_contents[dir_checksum]
                    logger.debug(
                        "Indexing pushed dir '{}' with "
                        "'{}' nested files".format(
                            dir_checksum, len(file_checksums)
                        )
                    )
                    remote.index.update([dir_checksum], file_checksums)

        return len(dir_plans[0]) + len(file_plans[0])

    @staticmethod
    def _dir_upload(func, futures, from_info, to_info, name):
        for future in as_completed(futures):
            if future.result():
                # do not upload this .dir file if any file in this
                # directory failed to upload
                logger.debug(
                    "failed to upload full contents of '{}', "
                    "aborting .dir file upload".format(name)
                )
                logger.error(f"failed to upload '{from_info}' to '{to_info}'")
                return 1
        return func(from_info, to_info, name)

    @index_locked
    def push(self, named_cache, remote, jobs=None, show_checksums=False):
        return self._process(
            named_cache,
            remote,
            jobs=jobs,
            show_checksums=show_checksums,
            download=False,
        )

    @index_locked
    def pull(self, named_cache, remote, jobs=None, show_checksums=False):
        return self._process(
            named_cache,
            remote,
            jobs=jobs,
            show_checksums=show_checksums,
            download=True,
        )

    @staticmethod
    def _log_missing_caches(checksum_info_dict):
        missing_caches = [
            (md5, info)
            for md5, info in checksum_info_dict.items()
            if info["status"] == STATUS_MISSING
        ]
        if missing_caches:
            missing_desc = "".join(
                "\nname: {}, md5: {}".format(info["name"], md5)
                for md5, info in missing_caches
            )
            msg = (
                "Some of the cache files do not exist neither locally "
                "nor on remote. Missing cache files: {}".format(missing_desc)
            )
            logger.warning(msg)

    def _unprotect_file(self, path):
        if System.is_symlink(path) or System.is_hardlink(path):
            logger.debug(f"Unprotecting '{path}'")
            tmp = os.path.join(os.path.dirname(path), "." + uuid())

            # The operations order is important here - if some application
            # would access the file during the process of copyfile then it
            # would get only the part of file. So, at first, the file should be
            # copied with the temporary name, and then original file should be
            # replaced by new.
            copyfile(path, tmp, name="Unprotecting '{}'".format(relpath(path)))
            remove(path)
            os.rename(tmp, path)

        else:
            logger.debug(
                "Skipping copying for '{}', since it is not "
                "a symlink or a hardlink.".format(path)
            )

        os.chmod(path, self.tree.file_mode)

    def _unprotect_dir(self, path):
        assert is_working_tree(self.repo.tree)

        for fname in self.repo.tree.walk_files(path):
            self._unprotect_file(fname)

    def unprotect(self, path_info):
        path = path_info.fspath
        if not os.path.exists(path):
            raise DvcException(f"can't unprotect non-existing data '{path}'")

        if os.path.isdir(path):
            self._unprotect_dir(path)
        else:
            self._unprotect_file(path)

    def protect(self, path_info):
        path = os.fspath(path_info)
        mode = self.CACHE_MODE

        try:
            os.chmod(path, mode)
        except OSError as exc:
            # There is nothing we need to do in case of a read-only file system
            if exc.errno == errno.EROFS:
                return

            # In shared cache scenario, we might not own the cache file, so we
            # need to check if cache file is already protected.
            if exc.errno not in [errno.EPERM, errno.EACCES]:
                raise

            actual = stat.S_IMODE(os.stat(path).st_mode)
            if actual != mode:
                raise

    def _remove_unpacked_dir(self, checksum):
        info = self.checksum_to_path_info(checksum)
        path_info = info.with_name(info.name + self.UNPACKED_DIR_SUFFIX)
        self.tree.remove(path_info)

    def is_protected(self, path_info):
        try:
            mode = os.stat(path_info).st_mode
        except FileNotFoundError:
            return False

        return stat.S_IMODE(mode) == self.CACHE_MODE
