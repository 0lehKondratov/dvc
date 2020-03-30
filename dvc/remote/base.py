import errno
import itertools
import json
import logging
import tempfile
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from functools import partial
from multiprocessing import cpu_count
from operator import itemgetter

from shortuuid import uuid

import dvc.prompt as prompt
from dvc.exceptions import (
    CheckoutError,
    DvcException,
    ConfirmRemoveError,
    DvcIgnoreInCollectedDirError,
    RemoteCacheRequiredError,
)
from dvc.ignore import DvcIgnore
from dvc.path_info import PathInfo, URLInfo
from dvc.progress import Tqdm
from dvc.remote.slow_link_detection import slow_link_guard
from dvc.state import StateNoop
from dvc.utils import tmp_fname
from dvc.utils.fs import move, makedirs
from dvc.utils.http import open_url

logger = logging.getLogger(__name__)

STATUS_OK = 1
STATUS_MISSING = 2
STATUS_NEW = 3
STATUS_DELETED = 4

STATUS_MAP = {
    # (local_exists, remote_exists)
    (True, True): STATUS_OK,
    (False, False): STATUS_MISSING,
    (True, False): STATUS_NEW,
    (False, True): STATUS_DELETED,
}


class RemoteCmdError(DvcException):
    def __init__(self, remote, cmd, ret, err):
        super().__init__(
            "{remote} command '{cmd}' finished with non-zero return code"
            " {ret}': {err}".format(remote=remote, cmd=cmd, ret=ret, err=err)
        )


class RemoteActionNotImplemented(DvcException):
    def __init__(self, action, scheme):
        m = "{} is not supported for {} remotes".format(action, scheme)
        super().__init__(m)


class RemoteMissingDepsError(DvcException):
    pass


class DirCacheError(DvcException):
    def __init__(self, checksum):
        super().__init__(
            "Failed to load dir cache for hash value: '{}'.".format(checksum)
        )


class RemoteBASE(object):
    scheme = "base"
    path_cls = URLInfo
    REQUIRES = {}
    JOBS = 4 * cpu_count()

    PARAM_RELPATH = "relpath"
    CHECKSUM_DIR_SUFFIX = ".dir"
    CHECKSUM_JOBS = max(1, min(4, cpu_count() // 2))
    DEFAULT_CACHE_TYPES = ["copy"]
    DEFAULT_VERIFY = False
    LIST_OBJECT_PAGE_SIZE = 1000
    TRAVERSE_WEIGHT_MULTIPLIER = 20
    TRAVERSE_PREFIX_LEN = 3
    TRAVERSE_THRESHOLD_SIZE = 500000
    CAN_TRAVERSE = True

    CACHE_MODE = None
    SHARED_MODE_MAP = {None: (None, None), "group": (None, None)}

    state = StateNoop()

    def __init__(self, repo, config):
        self.repo = repo

        self._check_requires(config)

        shared = config.get("shared")
        self._file_mode, self._dir_mode = self.SHARED_MODE_MAP[shared]

        self.checksum_jobs = (
            config.get("checksum_jobs")
            or (self.repo and self.repo.config["core"].get("checksum_jobs"))
            or self.CHECKSUM_JOBS
        )
        self.verify = config.get("verify", self.DEFAULT_VERIFY)
        self._dir_info = {}

        self.cache_types = config.get("type") or copy(self.DEFAULT_CACHE_TYPES)
        self.cache_type_confirmed = False

    @classmethod
    def get_missing_deps(cls):
        import importlib

        missing = []
        for package, module in cls.REQUIRES.items():
            try:
                importlib.import_module(module)
            except ImportError:
                missing.append(package)

        return missing

    def _check_requires(self, config):
        missing = self.get_missing_deps()
        if not missing:
            return

        url = config.get("url", "{}://".format(self.scheme))
        msg = (
            "URL '{}' is supported but requires these missing "
            "dependencies: {}. If you have installed dvc using pip, "
            "choose one of these options to proceed: \n"
            "\n"
            "    1) Install specific missing dependencies:\n"
            "        pip install {}\n"
            "    2) Install dvc package that includes those missing "
            "dependencies: \n"
            "        pip install 'dvc[{}]'\n"
            "    3) Install dvc package with all possible "
            "dependencies included: \n"
            "        pip install 'dvc[all]'\n"
            "\n"
            "If you have installed dvc from a binary package and you "
            "are still seeing this message, please report it to us "
            "using https://github.com/iterative/dvc/issues. Thank you!"
        ).format(url, missing, " ".join(missing), self.scheme)
        raise RemoteMissingDepsError(msg)

    def __repr__(self):
        return "{class_name}: '{path_info}'".format(
            class_name=type(self).__name__,
            path_info=self.path_info or "No path",
        )

    @classmethod
    def supported(cls, config):
        if isinstance(config, (str, bytes)):
            url = config
        else:
            url = config["url"]

        # NOTE: silently skipping remote, calling code should handle that
        parsed = urlparse(url)
        return parsed.scheme == cls.scheme

    @property
    def cache(self):
        return getattr(self.repo.cache, self.scheme)

    def get_file_checksum(self, path_info):
        raise NotImplementedError

    def _calculate_checksums(self, file_infos):
        file_infos = list(file_infos)
        with Tqdm(
            total=len(file_infos),
            unit="md5",
            desc="Computing file/dir hashes (only done once)",
        ) as pbar:
            worker = pbar.wrap_fn(self.get_file_checksum)
            with ThreadPoolExecutor(
                max_workers=self.checksum_jobs
            ) as executor:
                tasks = executor.map(worker, file_infos)
                checksums = dict(zip(file_infos, tasks))
        return checksums

    def _collect_dir(self, path_info):
        file_infos = set()

        for fname in self.walk_files(path_info):
            if DvcIgnore.DVCIGNORE_FILE == fname.name:
                raise DvcIgnoreInCollectedDirError(fname.parent)

            file_infos.add(fname)

        checksums = {fi: self.state.get(fi) for fi in file_infos}
        not_in_state = {
            fi for fi, checksum in checksums.items() if checksum is None
        }

        new_checksums = self._calculate_checksums(not_in_state)

        checksums.update(new_checksums)

        result = [
            {
                self.PARAM_CHECKSUM: checksums[fi],
                # NOTE: this is lossy transformation:
                #   "hey\there" -> "hey/there"
                #   "hey/there" -> "hey/there"
                # The latter is fine filename on Windows, which
                # will transform to dir/file on back transform.
                #
                # Yes, this is a BUG, as long as we permit "/" in
                # filenames on Windows and "\" on Unix
                self.PARAM_RELPATH: fi.relative_to(path_info).as_posix(),
            }
            for fi in file_infos
        ]

        # Sorting the list by path to ensure reproducibility
        return sorted(result, key=itemgetter(self.PARAM_RELPATH))

    def get_dir_checksum(self, path_info):
        if not self.cache:
            raise RemoteCacheRequiredError(path_info)

        dir_info = self._collect_dir(path_info)
        checksum, tmp_info = self._get_dir_info_checksum(dir_info)
        new_info = self.cache.checksum_to_path_info(checksum)
        if self.cache.changed_cache_file(checksum):
            self.cache.makedirs(new_info.parent)
            self.cache.move(tmp_info, new_info, mode=self.CACHE_MODE)

        self.state.save(path_info, checksum)
        self.state.save(new_info, checksum)

        return checksum

    def _get_dir_info_checksum(self, dir_info):
        tmp = tempfile.NamedTemporaryFile(delete=False).name
        with open(tmp, "w+") as fobj:
            json.dump(dir_info, fobj, sort_keys=True)

        from_info = PathInfo(tmp)
        to_info = self.cache.path_info / tmp_fname("")
        self.cache.upload(from_info, to_info, no_progress_bar=True)

        checksum = self.get_file_checksum(to_info) + self.CHECKSUM_DIR_SUFFIX
        return checksum, to_info

    def get_dir_cache(self, checksum):
        assert checksum

        dir_info = self._dir_info.get(checksum)
        if dir_info:
            return dir_info

        try:
            dir_info = self.load_dir_cache(checksum)
        except DirCacheError:
            dir_info = []

        self._dir_info[checksum] = dir_info
        return dir_info

    def load_dir_cache(self, checksum):
        path_info = self.checksum_to_path_info(checksum)

        try:
            with self.cache.open(path_info, "r") as fobj:
                d = json.load(fobj)
        except (ValueError, FileNotFoundError) as exc:
            raise DirCacheError(checksum) from exc

        if not isinstance(d, list):
            logger.error(
                "dir cache file format error '%s' [skipping the file]",
                path_info,
            )
            return []

        for info in d:
            # NOTE: here is a BUG, see comment to .as_posix() below
            relative_path = PathInfo.from_posix(info[self.PARAM_RELPATH])
            info[self.PARAM_RELPATH] = relative_path.fspath

        return d

    @classmethod
    def is_dir_checksum(cls, checksum):
        return checksum.endswith(cls.CHECKSUM_DIR_SUFFIX)

    def get_checksum(self, path_info):
        assert path_info.scheme == self.scheme

        if not self.exists(path_info):
            return None

        checksum = self.state.get(path_info)

        # If we have dir checksum in state db, but dir cache file is lost,
        # then we need to recollect the dir via .get_dir_checksum() call below,
        # see https://github.com/iterative/dvc/issues/2219 for context
        if (
            checksum
            and self.is_dir_checksum(checksum)
            and not self.exists(self.cache.checksum_to_path_info(checksum))
        ):
            checksum = None

        if checksum:
            return checksum

        if self.isdir(path_info):
            checksum = self.get_dir_checksum(path_info)
        else:
            checksum = self.get_file_checksum(path_info)

        if checksum:
            self.state.save(path_info, checksum)

        return checksum

    def save_info(self, path_info):
        return {self.PARAM_CHECKSUM: self.get_checksum(path_info)}

    def changed(self, path_info, checksum_info):
        """Checks if data has changed.

        A file is considered changed if:
            - It doesn't exist on the working directory (was unlinked)
            - Hash value is not computed (saving a new file)
            - The hash value stored is different from the given one
            - There's no file in the cache

        Args:
            path_info: dict with path information.
            checksum: expected hash value for this data.

        Returns:
            bool: True if data has changed, False otherwise.
        """

        logger.debug(
            "checking if '%s'('%s') has changed.", path_info, checksum_info
        )

        if not self.exists(path_info):
            logger.debug("'%s' doesn't exist.", path_info)
            return True

        checksum = checksum_info.get(self.PARAM_CHECKSUM)
        if checksum is None:
            logger.debug("hash value for '%s' is missing.", path_info)
            return True

        if self.changed_cache(checksum):
            logger.debug(
                "cache for '%s'('%s') has changed.", path_info, checksum
            )
            return True

        actual = self.get_checksum(path_info)
        if checksum != actual:
            logger.debug(
                "hash value '%s' for '%s' has changed (actual '%s').",
                checksum,
                actual,
                path_info,
            )
            return True

        logger.debug("'%s' hasn't changed.", path_info)
        return False

    def link(self, from_info, to_info):
        self._link(from_info, to_info, self.cache_types)

    def _link(self, from_info, to_info, link_types):
        assert self.isfile(from_info)

        self.makedirs(to_info.parent)

        self._try_links(from_info, to_info, link_types)

    def _verify_link(self, path_info, link_type):
        if self.cache_type_confirmed:
            return

        is_link = getattr(self, "is_{}".format(link_type), None)
        if is_link and not is_link(path_info):
            self.remove(path_info)
            raise DvcException("failed to verify {}".format(link_type))

        self.cache_type_confirmed = True

    @slow_link_guard
    def _try_links(self, from_info, to_info, link_types):
        while link_types:
            link_method = getattr(self, link_types[0])
            try:
                self._do_link(from_info, to_info, link_method)
                self._verify_link(to_info, link_types[0])
                return

            except DvcException as exc:
                logger.debug(
                    "Cache type '%s' is not supported: %s", link_types[0], exc
                )
                del link_types[0]

        raise DvcException("no possible cache types left to try out.")

    def _do_link(self, from_info, to_info, link_method):
        if self.exists(to_info):
            raise DvcException("Link '{}' already exists!".format(to_info))

        link_method(from_info, to_info)

        logger.debug(
            "Created '%s': %s -> %s", self.cache_types[0], from_info, to_info,
        )

    def _save_file(self, path_info, checksum, save_link=True):
        assert checksum

        cache_info = self.checksum_to_path_info(checksum)
        if self.changed_cache(checksum):
            self.move(path_info, cache_info, mode=self.CACHE_MODE)
            self.link(cache_info, path_info)
        elif self.iscopy(path_info) and self._cache_is_copy(path_info):
            # Default relink procedure involves unneeded copy
            self.unprotect(path_info)
        else:
            self.remove(path_info)
            self.link(cache_info, path_info)

        if save_link:
            self.state.save_link(path_info)

        # we need to update path and cache, since in case of reflink,
        # or copy cache type moving original file results in updates on
        # next executed command, which causes md5 recalculation
        self.state.save(path_info, checksum)
        self.state.save(cache_info, checksum)

    def _cache_is_copy(self, path_info):
        """Checks whether cache uses copies."""
        if self.cache_type_confirmed:
            return self.cache_types[0] == "copy"

        if set(self.cache_types) <= {"copy"}:
            return True

        workspace_file = path_info.with_name("." + uuid())
        test_cache_file = self.path_info / ".cache_type_test_file"
        if not self.exists(test_cache_file):
            with self.open(test_cache_file, "wb") as fobj:
                fobj.write(bytes(1))
        try:
            self.link(test_cache_file, workspace_file)
        finally:
            self.remove(workspace_file)
            self.remove(test_cache_file)

        self.cache_type_confirmed = True
        return self.cache_types[0] == "copy"

    def _save_dir(self, path_info, checksum, save_link=True):
        cache_info = self.checksum_to_path_info(checksum)
        dir_info = self.get_dir_cache(checksum)

        for entry in Tqdm(
            dir_info, desc="Saving " + path_info.name, unit="file"
        ):
            entry_info = path_info / entry[self.PARAM_RELPATH]
            entry_checksum = entry[self.PARAM_CHECKSUM]
            self._save_file(entry_info, entry_checksum, save_link=False)

        if save_link:
            self.state.save_link(path_info)

        self.state.save(cache_info, checksum)
        self.state.save(path_info, checksum)

    def is_empty(self, path_info):
        return False

    def isfile(self, path_info):
        """Optional: Overwrite only if the remote has a way to distinguish
        between a directory and a file.
        """
        return True

    def isdir(self, path_info):
        """Optional: Overwrite only if the remote has a way to distinguish
        between a directory and a file.
        """
        return False

    def iscopy(self, path_info):
        """Check if this file is an independent copy."""
        return False  # We can't be sure by default

    def walk_files(self, path_info):
        """Return a generator with `PathInfo`s to all the files"""
        raise NotImplementedError

    @staticmethod
    def protect(path_info):
        pass

    def save(self, path_info, checksum_info, save_link=True):
        if path_info.scheme != self.scheme:
            raise RemoteActionNotImplemented(
                "save {} -> {}".format(path_info.scheme, self.scheme),
                self.scheme,
            )

        checksum = checksum_info[self.PARAM_CHECKSUM]
        self._save(path_info, checksum, save_link)

    def _save(self, path_info, checksum, save_link=True):
        to_info = self.checksum_to_path_info(checksum)
        logger.debug("Saving '%s' to '%s'.", path_info, to_info)
        if self.isdir(path_info):
            self._save_dir(path_info, checksum, save_link)
            return
        self._save_file(path_info, checksum, save_link)

    def _handle_transfer_exception(
        self, from_info, to_info, exception, operation
    ):
        if isinstance(exception, OSError) and exception.errno == errno.EMFILE:
            raise exception

        logger.exception(
            "failed to %s '%s' to '%s'", operation, from_info, to_info
        )
        return 1

    def upload(self, from_info, to_info, name=None, no_progress_bar=False):
        if not hasattr(self, "_upload"):
            raise RemoteActionNotImplemented("upload", self.scheme)

        if to_info.scheme != self.scheme:
            raise NotImplementedError

        if from_info.scheme != "local":
            raise NotImplementedError

        logger.debug("Uploading '%s' to '%s'", from_info, to_info)

        name = name or from_info.name

        try:
            self._upload(
                from_info.fspath,
                to_info,
                name=name,
                no_progress_bar=no_progress_bar,
            )
        except Exception as e:
            return self._handle_transfer_exception(
                from_info, to_info, e, "upload"
            )

        return 0

    def download(
        self,
        from_info,
        to_info,
        name=None,
        no_progress_bar=False,
        file_mode=None,
        dir_mode=None,
    ):
        if not hasattr(self, "_download"):
            raise RemoteActionNotImplemented("download", self.scheme)

        if from_info.scheme != self.scheme:
            raise NotImplementedError

        if to_info.scheme == self.scheme != "local":
            self.copy(from_info, to_info)
            return 0

        if to_info.scheme != "local":
            raise NotImplementedError

        if self.isdir(from_info):
            return self._download_dir(
                from_info, to_info, name, no_progress_bar, file_mode, dir_mode
            )
        return self._download_file(
            from_info, to_info, name, no_progress_bar, file_mode, dir_mode
        )

    def _download_dir(
        self, from_info, to_info, name, no_progress_bar, file_mode, dir_mode
    ):
        from_infos = list(self.walk_files(from_info))
        to_infos = (
            to_info / info.relative_to(from_info) for info in from_infos
        )

        with Tqdm(
            total=len(from_infos),
            desc="Downloading directory",
            unit="Files",
            disable=no_progress_bar,
        ) as pbar:
            download_files = pbar.wrap_fn(
                partial(
                    self._download_file,
                    name=name,
                    no_progress_bar=True,
                    file_mode=file_mode,
                    dir_mode=dir_mode,
                )
            )
            with ThreadPoolExecutor(max_workers=self.JOBS) as executor:
                futures = executor.map(download_files, from_infos, to_infos)
                return sum(futures)

    def _download_file(
        self, from_info, to_info, name, no_progress_bar, file_mode, dir_mode
    ):
        makedirs(to_info.parent, exist_ok=True, mode=dir_mode)

        logger.debug("Downloading '%s' to '%s'", from_info, to_info)
        name = name or to_info.name

        tmp_file = tmp_fname(to_info)

        try:
            self._download(
                from_info, tmp_file, name=name, no_progress_bar=no_progress_bar
            )
        except Exception as e:
            return self._handle_transfer_exception(
                from_info, to_info, e, "download"
            )

        move(tmp_file, to_info, mode=file_mode)

        return 0

    def open(self, path_info, mode="r", encoding=None):
        if hasattr(self, "_generate_download_url"):
            get_url = partial(self._generate_download_url, path_info)
            return open_url(get_url, mode=mode, encoding=encoding)

        raise RemoteActionNotImplemented("open", self.scheme)

    def remove(self, path_info):
        raise RemoteActionNotImplemented("remove", self.scheme)

    def move(self, from_info, to_info, mode=None):
        assert mode is None
        self.copy(from_info, to_info)
        self.remove(from_info)

    def copy(self, from_info, to_info):
        raise RemoteActionNotImplemented("copy", self.scheme)

    def symlink(self, from_info, to_info):
        raise RemoteActionNotImplemented("symlink", self.scheme)

    def hardlink(self, from_info, to_info):
        raise RemoteActionNotImplemented("hardlink", self.scheme)

    def reflink(self, from_info, to_info):
        raise RemoteActionNotImplemented("reflink", self.scheme)

    def exists(self, path_info):
        raise NotImplementedError

    def path_to_checksum(self, path):
        parts = self.path_cls(path).parts[-2:]

        if not (len(parts) == 2 and parts[0] and len(parts[0]) == 2):
            raise ValueError("Bad cache file path")

        return "".join(parts)

    def checksum_to_path_info(self, checksum):
        return self.path_info / checksum[0:2] / checksum[2:]

    def list_cache_paths(self, prefix=None, progress_callback=None):
        raise NotImplementedError

    def all(self):
        # NOTE: The list might be way too big(e.g. 100M entries, md5 for each
        # is 32 bytes, so ~3200Mb list) and we don't really need all of it at
        # the same time, so it makes sense to use a generator to gradually
        # iterate over it, without keeping all of it in memory.
        for path in self.list_cache_paths():
            try:
                yield self.path_to_checksum(path)
            except ValueError:
                # We ignore all the non-cache looking files
                pass

    def gc(self, named_cache):
        used = self.extract_used_local_checksums(named_cache)

        if self.scheme != "":
            used.update(named_cache[self.scheme])

        removed = False
        for checksum in self.all():
            if checksum in used:
                continue
            path_info = self.checksum_to_path_info(checksum)
            if self.is_dir_checksum(checksum):
                self._remove_unpacked_dir(checksum)
            self.remove(path_info)
            removed = True
        return removed

    def is_protected(self, path_info):
        return False

    def changed_cache_file(self, checksum):
        """Compare the given checksum with the (corresponding) actual one.

        - Use `State` as a cache for computed checksums
            + The entries are invalidated by taking into account the following:
                * mtime
                * inode
                * size
                * checksum

        - Remove the file from cache if it doesn't match the actual checksum
        """

        cache_info = self.checksum_to_path_info(checksum)
        if self.is_protected(cache_info):
            logger.debug(
                "Assuming '%s' is unchanged since it is read-only", cache_info
            )
            return False

        actual = self.get_checksum(cache_info)

        logger.debug(
            "cache '%s' expected '%s' actual '%s'",
            cache_info,
            checksum,
            actual,
        )

        if not checksum or not actual:
            return True

        if actual.split(".")[0] == checksum.split(".")[0]:
            # making cache file read-only so we don't need to check it
            # next time
            self.protect(cache_info)
            return False

        if self.exists(cache_info):
            logger.warning("corrupted cache file '%s'.", cache_info)
            self.remove(cache_info)

        return True

    def _changed_dir_cache(self, checksum, path_info=None, filter_info=None):
        if self.changed_cache_file(checksum):
            return True

        if not (path_info and filter_info) and not self._changed_unpacked_dir(
            checksum
        ):
            return False

        for entry in self.get_dir_cache(checksum):
            entry_checksum = entry[self.PARAM_CHECKSUM]

            if path_info and filter_info:
                entry_info = path_info / entry[self.PARAM_RELPATH]
                if not entry_info.isin_or_eq(filter_info):
                    continue

            if self.changed_cache_file(entry_checksum):
                return True

        if not (path_info and filter_info):
            self._update_unpacked_dir(checksum)

        return False

    def changed_cache(self, checksum, path_info=None, filter_info=None):
        if self.is_dir_checksum(checksum):
            return self._changed_dir_cache(
                checksum, path_info=path_info, filter_info=filter_info
            )
        return self.changed_cache_file(checksum)

    def cache_exists(self, checksums, jobs=None, name=None):
        """Check if the given checksums are stored in the remote.

        There are two ways of performing this check:

        - Traverse method: Get a list of all the files in the remote
            (traversing the cache directory) and compare it with
            the given checksums. Cache entries will be retrieved in parallel
            threads according to prefix (i.e. entries starting with, "00...",
            "01...", and so on) and a progress bar will be displayed.

        - Exists method: For each given checksum, run the `exists`
            method and filter the checksums that aren't on the remote.
            This is done in parallel threads.
            It also shows a progress bar when performing the check.

        The reason for such an odd logic is that most of the remotes
        take much shorter time to just retrieve everything they have under
        a certain prefix (e.g. s3, gs, ssh, hdfs). Other remotes that can
        check if particular file exists much quicker, use their own
        implementation of cache_exists (see ssh, local).

        Which method to use will be automatically determined after estimating
        the size of the remote cache, and comparing the estimated size with
        len(checksums). To estimate the size of the remote cache, we fetch
        a small subset of cache entries (i.e. entries starting with "00...").
        Based on the number of entries in that subset, the size of the full
        cache can be estimated, since the cache is evenly distributed according
        to checksum.

        Returns:
            A list with checksums that were found in the remote
        """
        # Remotes which do not use traverse prefix should override
        # cache_exists() (see ssh, local)
        assert self.TRAVERSE_PREFIX_LEN >= 2

        if len(checksums) == 1 or not self.CAN_TRAVERSE:
            return self._cache_object_exists(checksums, jobs, name)

        checksums = frozenset(checksums)

        # Max remote size allowed for us to use traverse method
        remote_size, remote_checksums = self._estimate_cache_size(
            checksums, name=name
        )

        traverse_pages = remote_size / self.LIST_OBJECT_PAGE_SIZE
        # For sufficiently large remotes, traverse must be weighted to account
        # for performance overhead from large lists/sets.
        # From testing with S3, for remotes with 1M+ files, object_exists is
        # faster until len(checksums) is at least 10k~100k
        if remote_size > self.TRAVERSE_THRESHOLD_SIZE:
            traverse_weight = traverse_pages * self.TRAVERSE_WEIGHT_MULTIPLIER
        else:
            traverse_weight = traverse_pages
        if len(checksums) < traverse_weight:
            logger.debug(
                "Large remote ('{}' checksums < '{}' traverse weight), "
                "using object_exists for remaining checksums".format(
                    len(checksums), traverse_weight
                )
            )
            return list(
                checksums & remote_checksums
            ) + self._cache_object_exists(
                checksums - remote_checksums, jobs, name
            )

        if traverse_pages < 256 / self.JOBS:
            # Threaded traverse will require making at least 255 more requests
            # to the remote, so for small enough remotes, fetching the entire
            # list at once will require fewer requests (but also take into
            # account that this must be done sequentially rather than in
            # parallel)
            logger.debug(
                "Querying {} checksums via default traverse".format(
                    len(checksums)
                )
            )
            return list(checksums & set(self.all()))

        return self._cache_exists_traverse(
            checksums, remote_checksums, remote_size, jobs, name
        )

    def _cache_paths_with_max(
        self, max_paths, prefix=None, progress_callback=None
    ):
        count = 0
        for path in self.list_cache_paths(prefix, progress_callback):
            yield path
            count += 1
            if count > max_paths:
                logger.debug(
                    "list_cache_paths() returned max '{}' paths, "
                    "skipping remaining results".format(max_paths)
                )
                return

    def _max_estimation_size(self, checksums):
        # Max remote size allowed for us to use traverse method
        return max(
            self.TRAVERSE_THRESHOLD_SIZE,
            len(checksums)
            / self.TRAVERSE_WEIGHT_MULTIPLIER
            * self.LIST_OBJECT_PAGE_SIZE,
        )

    def _estimate_cache_size(self, checksums, short_circuit=True, name=None):
        """Estimate remote cache size based on number of entries beginning with
        "00..." prefix.
        """
        prefix = "0" * self.TRAVERSE_PREFIX_LEN
        total_prefixes = pow(16, self.TRAVERSE_PREFIX_LEN)
        if short_circuit:
            max_remote_size = self._max_estimation_size(checksums)
        else:
            max_remote_size = None

        with Tqdm(
            desc="Estimating size of "
            + ("cache in '{}'".format(name) if name else "remote cache"),
            unit="file",
            total=max_remote_size,
        ) as pbar:

            def update(n=1):
                pbar.update(n * total_prefixes)

            if max_remote_size:
                paths = self._cache_paths_with_max(
                    max_remote_size / total_prefixes, prefix, update
                )
            else:
                paths = self.list_cache_paths(prefix, update)

            remote_checksums = set(map(self.path_to_checksum, paths))
            if remote_checksums:
                remote_size = total_prefixes * len(remote_checksums)
            else:
                remote_size = total_prefixes
            logger.debug("Estimated remote size: {} files".format(remote_size))
        return remote_size, remote_checksums

    def _cache_exists_traverse(
        self, checksums, remote_checksums, remote_size, jobs=None, name=None
    ):
        logger.debug(
            "Querying {} checksums via threaded traverse".format(
                len(checksums)
            )
        )

        traverse_prefixes = ["{:02x}".format(i) for i in range(1, 256)]
        if self.TRAVERSE_PREFIX_LEN > 2:
            traverse_prefixes += [
                "{0:0{1}x}".format(i, self.TRAVERSE_PREFIX_LEN)
                for i in range(1, pow(16, self.TRAVERSE_PREFIX_LEN - 2))
            ]
        with Tqdm(
            desc="Querying "
            + ("cache in '{}'".format(name) if name else "remote cache"),
            total=remote_size,
            initial=len(remote_checksums),
            unit="objects",
        ) as pbar:

            def list_with_update(prefix):
                paths = self.list_cache_paths(
                    prefix=prefix, progress_callback=pbar.update
                )
                return map(self.path_to_checksum, list(paths))

            with ThreadPoolExecutor(max_workers=jobs or self.JOBS) as executor:
                in_remote = executor.map(list_with_update, traverse_prefixes,)
                remote_checksums.update(
                    itertools.chain.from_iterable(in_remote)
                )
            return list(checksums & remote_checksums)

    def _cache_object_exists(self, checksums, jobs=None, name=None):
        logger.debug(
            "Querying {} checksums via object_exists".format(len(checksums))
        )
        with Tqdm(
            desc="Querying "
            + ("cache in " + name if name else "remote cache"),
            total=len(checksums),
            unit="file",
        ) as pbar:

            def exists_with_progress(path_info):
                ret = self.exists(path_info)
                pbar.update_desc(str(path_info))
                return ret

            with ThreadPoolExecutor(max_workers=jobs or self.JOBS) as executor:
                path_infos = map(self.checksum_to_path_info, checksums)
                in_remote = executor.map(exists_with_progress, path_infos)
                ret = list(itertools.compress(checksums, in_remote))
                return ret

    def already_cached(self, path_info):
        current = self.get_checksum(path_info)

        if not current:
            return False

        return not self.changed_cache(current)

    def safe_remove(self, path_info, force=False):
        if not self.exists(path_info):
            return

        if not force and not self.already_cached(path_info):
            msg = (
                "file '{}' is going to be removed."
                " Are you sure you want to proceed?".format(str(path_info))
            )

            if not prompt.confirm(msg):
                raise ConfirmRemoveError(str(path_info))

        self.remove(path_info)

    def _checkout_file(
        self, path_info, checksum, force, progress_callback=None, relink=False
    ):
        """The file is changed we need to checkout a new copy"""
        added, modified = True, False
        cache_info = self.checksum_to_path_info(checksum)
        if self.exists(path_info):
            logger.debug("data '%s' will be replaced.", path_info)
            self.safe_remove(path_info, force=force)
            added, modified = False, True

        self.link(cache_info, path_info)
        self.state.save_link(path_info)
        self.state.save(path_info, checksum)
        if progress_callback:
            progress_callback(str(path_info))

        return added, modified and not relink

    def makedirs(self, path_info):
        """Optional: Implement only if the remote needs to create
        directories before copying/linking/moving data
        """

    def _checkout_dir(
        self,
        path_info,
        checksum,
        force,
        progress_callback=None,
        relink=False,
        filter_info=None,
    ):
        added, modified = False, False
        # Create dir separately so that dir is created
        # even if there are no files in it
        if not self.exists(path_info):
            added = True
            self.makedirs(path_info)

        dir_info = self.get_dir_cache(checksum)

        logger.debug("Linking directory '%s'.", path_info)

        for entry in dir_info:
            relative_path = entry[self.PARAM_RELPATH]
            entry_checksum = entry[self.PARAM_CHECKSUM]
            entry_cache_info = self.checksum_to_path_info(entry_checksum)
            entry_info = path_info / relative_path

            if filter_info and not entry_info.isin_or_eq(filter_info):
                continue

            entry_checksum_info = {self.PARAM_CHECKSUM: entry_checksum}
            if relink or self.changed(entry_info, entry_checksum_info):
                modified = True
                self.safe_remove(entry_info, force=force)
                self.link(entry_cache_info, entry_info)
                self.state.save(entry_info, entry_checksum)
            if progress_callback:
                progress_callback(str(entry_info))

        modified = (
            self._remove_redundant_files(path_info, dir_info, force)
            or modified
        )

        self.state.save_link(path_info)
        self.state.save(path_info, checksum)

        # relink is not modified, assume it as nochange
        return added, not added and modified and not relink

    def _remove_redundant_files(self, path_info, dir_info, force):
        existing_files = set(self.walk_files(path_info))

        needed_files = {
            path_info / entry[self.PARAM_RELPATH] for entry in dir_info
        }
        redundant_files = existing_files - needed_files
        for path in redundant_files:
            self.safe_remove(path, force)

        return bool(redundant_files)

    def checkout(
        self,
        path_info,
        checksum_info,
        force=False,
        progress_callback=None,
        relink=False,
        filter_info=None,
    ):
        if path_info.scheme not in ["local", self.scheme]:
            raise NotImplementedError

        checksum = checksum_info.get(self.PARAM_CHECKSUM)
        failed = None
        skip = False
        if not checksum:
            logger.warning(
                "No file hash info found for '%s'. " "It won't be created.",
                path_info,
            )
            self.safe_remove(path_info, force=force)
            failed = path_info

        elif not relink and not self.changed(path_info, checksum_info):
            logger.debug("Data '%s' didn't change.", path_info)
            skip = True

        elif self.changed_cache(
            checksum, path_info=path_info, filter_info=filter_info
        ):
            logger.warning(
                "Cache '%s' not found. File '%s' won't be created.",
                checksum,
                path_info,
            )
            self.safe_remove(path_info, force=force)
            failed = path_info

        if failed or skip:
            if progress_callback:
                progress_callback(
                    str(path_info),
                    self.get_files_number(
                        self.path_info, checksum, filter_info
                    ),
                )
            if failed:
                raise CheckoutError([failed])
            return

        logger.debug("Checking out '%s' with cache '%s'.", path_info, checksum)

        return self._checkout(
            path_info, checksum, force, progress_callback, relink, filter_info,
        )

    def _checkout(
        self,
        path_info,
        checksum,
        force=False,
        progress_callback=None,
        relink=False,
        filter_info=None,
    ):
        if not self.is_dir_checksum(checksum):
            return self._checkout_file(
                path_info, checksum, force, progress_callback, relink
            )

        return self._checkout_dir(
            path_info, checksum, force, progress_callback, relink, filter_info
        )

    def get_files_number(self, path_info, checksum, filter_info):
        from funcy.py3 import ilen

        if not checksum:
            return 0

        if not self.is_dir_checksum(checksum):
            return 1

        if not filter_info:
            return len(self.get_dir_cache(checksum))

        return ilen(
            filter_info.isin_or_eq(path_info / entry[self.PARAM_CHECKSUM])
            for entry in self.get_dir_cache(checksum)
        )

    @staticmethod
    def unprotect(path_info):
        pass

    def _get_unpacked_dir_names(self, checksums):
        return set()

    def extract_used_local_checksums(self, named_cache):
        used = set(named_cache["local"])
        unpacked = self._get_unpacked_dir_names(used)
        return used | unpacked

    def _changed_unpacked_dir(self, checksum):
        return True

    def _update_unpacked_dir(self, checksum):
        pass

    def _remove_unpacked_dir(self, checksum):
        pass
