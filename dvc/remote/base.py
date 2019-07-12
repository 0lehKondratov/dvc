from __future__ import unicode_literals

from dvc.ignore import DvcIgnore
from dvc.utils.compat import str, basestring, urlparse, fspath_py35, makedirs

import os
import json
import logging
import tempfile
import itertools
from contextlib import contextmanager
from operator import itemgetter
from multiprocessing import cpu_count
from concurrent.futures import as_completed, ThreadPoolExecutor

import dvc.prompt as prompt
from dvc.config import Config
from dvc.exceptions import (
    DvcException,
    ConfirmRemoveError,
    DvcIgnoreInCollectedDirError,
)
from dvc.progress import progress, ProgressCallback
from dvc.utils import LARGE_DIR_SIZE, tmp_fname, to_chunks, move, relpath
from dvc.state import StateBase
from dvc.path_info import PathInfo, URLInfo


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


CHECKSUM_JOBS = max(1, min(4, cpu_count() // 2))


class DataCloudError(DvcException):
    """ Data Cloud exception """

    def __init__(self, msg):
        super(DataCloudError, self).__init__("Data sync error: {}".format(msg))


class RemoteCmdError(DvcException):
    def __init__(self, remote, cmd, ret, err):
        super(RemoteCmdError, self).__init__(
            "{remote} command '{cmd}' finished with non-zero return code"
            " {ret}': {err}".format(remote=remote, cmd=cmd, ret=ret, err=err)
        )


class RemoteActionNotImplemented(DvcException):
    def __init__(self, action, scheme):
        m = "{} is not supported by {} remote".format(action, scheme)
        super(RemoteActionNotImplemented, self).__init__(m)


class RemoteMissingDepsError(DvcException):
    pass


class RemoteBASE(object):
    scheme = "base"
    path_cls = URLInfo
    REQUIRES = {}
    JOBS = 4 * cpu_count()

    PARAM_RELPATH = "relpath"
    CHECKSUM_DIR_SUFFIX = ".dir"

    def __init__(self, repo, config):
        self.repo = repo
        deps_ok = all(self.REQUIRES.values())
        if not deps_ok:
            missing = [k for k, v in self.REQUIRES.items() if v is None]
            url = config.get(
                Config.SECTION_REMOTE_URL, "{}://".format(self.scheme)
            )
            msg = (
                "URL '{}' is supported but requires these missing "
                "dependencies: {}. If you have installed dvc using pip, "
                "choose one of these options to proceed: \n"
                "\n"
                "    1) Install specific missing dependencies:\n"
                "        pip install {}\n"
                "    2) Install dvc package that includes those missing "
                "dependencies: \n"
                "        pip install dvc[{}]\n"
                "    3) Install dvc package with all possible "
                "dependencies included: \n"
                "        pip install dvc[all]\n"
                "\n"
                "If you have installed dvc from a binary package and you "
                "are still seeing this message, please report it to us "
                "using https://github.com/iterative/dvc/issues. Thank you!"
            ).format(url, missing, " ".join(missing), self.scheme)
            raise RemoteMissingDepsError(msg)

        core = config.get(Config.SECTION_CORE, {})
        self.checksum_jobs = core.get(
            Config.SECTION_CORE_CHECKSUM_JOBS, CHECKSUM_JOBS
        )

        self.protected = False
        self.no_traverse = config.get(Config.SECTION_REMOTE_NO_TRAVERSE)
        self.state = StateBase()
        self._dir_info = {}

    def __repr__(self):
        return "{class_name}: '{path_info}'".format(
            class_name=type(self).__name__,
            path_info=self.path_info or "No path",
        )

    def compat_config(config):
        return config.copy()

    @classmethod
    def supported(cls, config):
        if isinstance(config, basestring):
            url = config
        else:
            url = config[Config.SECTION_REMOTE_URL]

        # NOTE: silently skipping remote, calling code should handle that
        parsed = urlparse(url)
        return parsed.scheme == cls.scheme

    @property
    def cache(self):
        return getattr(self.repo.cache, self.scheme)

    def get_file_checksum(self, path_info):
        raise NotImplementedError

    def _collect_dir(self, path_info):
        dir_info = {}

        with ThreadPoolExecutor(max_workers=self.checksum_jobs) as executor:
            for root, _dirs, files in self.walk(path_info):
                root_info = path_info / root

                for fname in files:

                    if fname == DvcIgnore.DVCIGNORE_FILE:
                        raise DvcIgnoreInCollectedDirError(root)

                    file_info = root_info / fname
                    relative_path = file_info.relative_to(path_info)
                    checksum = executor.submit(
                        self.get_file_checksum, file_info
                    )
                    dir_info[checksum] = {
                        # NOTE: this is lossy transformation:
                        #   "hey\there" -> "hey/there"
                        #   "hey/there" -> "hey/there"
                        # The latter is fine filename on Windows, which
                        # will transform to dir/file on back transform.
                        #
                        # Yes, this is a BUG, as long as we permit "/" in
                        # filenames on Windows and "\" on Unix
                        self.PARAM_RELPATH: relative_path.as_posix()
                    }

        checksums = as_completed(dir_info)
        if len(dir_info) > LARGE_DIR_SIZE:
            msg = (
                "Computing md5 for a large number of files. "
                "This is only done once."
            )
            logger.info(msg)
            checksums = progress(checksums, total=len(dir_info))

        # NOTE: resolving futures
        for checksum in checksums:
            entry = dir_info[checksum]
            entry[self.PARAM_CHECKSUM] = checksum.result()

        # NOTE: sorting the list by path to ensure reproducibility
        return sorted(dir_info.values(), key=itemgetter(self.PARAM_RELPATH))

    def get_dir_checksum(self, path_info):
        dir_info = self._collect_dir(path_info)
        checksum, tmp_info = self._get_dir_info_checksum(dir_info)
        new_info = self.cache.checksum_to_path_info(checksum)
        if self.cache.changed_cache_file(checksum):
            self.cache.move(tmp_info, new_info)

        self.state.save(path_info, checksum)
        self.state.save(new_info, checksum)

        return checksum

    def _get_dir_info_checksum(self, dir_info):
        tmp = tempfile.NamedTemporaryFile(delete=False).name
        with open(tmp, "w+") as fobj:
            json.dump(dir_info, fobj, sort_keys=True)

        from_info = PathInfo(tmp)
        to_info = self.cache.path_info / tmp_fname("")
        self.cache.upload([from_info], [to_info], no_progress_bar=True)

        checksum = self.get_file_checksum(to_info) + self.CHECKSUM_DIR_SUFFIX
        return checksum, to_info

    def get_dir_cache(self, checksum):
        assert checksum

        dir_info = self._dir_info.get(checksum)
        if dir_info:
            return dir_info

        dir_info = self.load_dir_cache(checksum)
        self._dir_info[checksum] = dir_info
        return dir_info

    def load_dir_cache(self, checksum):
        path_info = self.checksum_to_path_info(checksum)

        fobj = tempfile.NamedTemporaryFile(delete=False)
        path = fobj.name
        to_info = PathInfo(path)
        self.cache.download([path_info], [to_info], no_progress_bar=True)

        try:
            with open(path, "r") as fobj:
                d = json.load(fobj)
        except ValueError:
            logger.exception("Failed to load dir cache '{}'".format(path_info))
            return []
        finally:
            os.unlink(path)

        if not isinstance(d, list):
            msg = "dir cache file format error '{}' [skipping the file]"
            logger.error(msg.format(relpath(path)))
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
        assert path_info.scheme == self.scheme
        return {self.PARAM_CHECKSUM: self.get_checksum(path_info)}

    def changed(self, path_info, checksum_info):
        """Checks if data has changed.

        A file is considered changed if:
            - It doesn't exist on the working directory (was unlinked)
            - Checksum is not computed (saving a new file)
            - The checkusm stored in the State is different from the given one
            - There's no file in the cache

        Args:
            path_info: dict with path information.
            checksum: expected checksum for this data.

        Returns:
            bool: True if data has changed, False otherwise.
        """

        logger.debug(
            "checking if '{}'('{}') has changed.".format(
                path_info, checksum_info
            )
        )

        if not self.exists(path_info):
            logger.debug("'{}' doesn't exist.".format(path_info))
            return True

        checksum = checksum_info.get(self.PARAM_CHECKSUM)
        if checksum is None:
            logger.debug("checksum for '{}' is missing.".format(path_info))
            return True

        if self.changed_cache(checksum):
            logger.debug(
                "cache for '{}'('{}') has changed.".format(path_info, checksum)
            )
            return True

        actual = self.save_info(path_info)[self.PARAM_CHECKSUM]
        if checksum != actual:
            logger.debug(
                "checksum '{}'(actual '{}') for '{}' has changed.".format(
                    checksum, actual, path_info
                )
            )
            return True

        logger.debug("'{}' hasn't changed.".format(path_info))
        return False

    def link(self, from_info, to_info, link_type=None):
        self.copy(from_info, to_info)

    def _save_file(self, path_info, checksum, save_link=True):
        assert checksum

        cache_info = self.checksum_to_path_info(checksum)
        if self.changed_cache(checksum):
            self.move(path_info, cache_info)
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

    def _save_dir(self, path_info, checksum):
        cache_info = self.checksum_to_path_info(checksum)
        dir_info = self.get_dir_cache(checksum)

        for entry in dir_info:
            entry_info = path_info / entry[self.PARAM_RELPATH]
            entry_checksum = entry[self.PARAM_CHECKSUM]
            self._save_file(entry_info, entry_checksum, save_link=False)

        self.state.save_link(path_info)
        self.state.save(cache_info, checksum)
        self.state.save(path_info, checksum)

    def is_empty(self, path_info):
        return False

    def isfile(self, path_info):
        raise NotImplementedError

    def isdir(self, path_info):
        return False

    def walk(self, path_info):
        raise NotImplementedError

    @staticmethod
    def protect(path_info):
        pass

    def save(self, path_info, checksum_info):
        if path_info.scheme != self.scheme:
            raise RemoteActionNotImplemented(
                "save {} -> {}".format(path_info.scheme, self.scheme),
                self.scheme,
            )

        checksum = checksum_info[self.PARAM_CHECKSUM]
        if not self.changed_cache(checksum):
            self._checkout(path_info, checksum)
            return

        self._save(path_info, checksum)

    def _save(self, path_info, checksum):
        to_info = self.checksum_to_path_info(checksum)
        logger.info("Saving '{}' to '{}'.".format(path_info, to_info))
        if self.isdir(path_info):
            self._save_dir(path_info, checksum)
            return
        self._save_file(path_info, checksum)

    @contextmanager
    def transfer_context(self):
        yield None

    def upload(self, from_infos, to_infos, names=None, no_progress_bar=False):
        if not hasattr(self, "_upload"):
            raise RemoteActionNotImplemented("upload", self.scheme)
        names = self._verify_path_args(to_infos, from_infos, names)
        fails = 0

        with self.transfer_context() as ctx:
            for from_info, to_info, name in zip(from_infos, to_infos, names):
                if to_info.scheme != self.scheme:
                    raise NotImplementedError

                if from_info.scheme != "local":
                    raise NotImplementedError

                msg = "Uploading '{}' to '{}'"
                logger.debug(msg.format(from_info, to_info))

                if not name:
                    name = from_info.name

                if not no_progress_bar:
                    progress.update_target(name, 0, None)

                try:
                    self._upload(
                        from_info.fspath,
                        to_info,
                        name=name,
                        ctx=ctx,
                        no_progress_bar=no_progress_bar,
                    )
                except Exception:
                    fails += 1
                    msg = "failed to upload '{}' to '{}'"
                    logger.exception(msg.format(from_info, to_info))
                    continue

                if not no_progress_bar:
                    progress.finish_target(name)

        return fails

    def download(
        self,
        from_infos,
        to_infos,
        names=None,
        no_progress_bar=False,
        resume=False,
    ):
        if not hasattr(self, "_download"):
            raise RemoteActionNotImplemented("download", self.scheme)

        names = self._verify_path_args(from_infos, to_infos, names)
        fails = 0

        with self.transfer_context() as ctx:
            for to_info, from_info, name in zip(to_infos, from_infos, names):
                if from_info.scheme != self.scheme:
                    raise NotImplementedError

                if to_info.scheme == self.scheme != "local":
                    self.copy(from_info, to_info, ctx=ctx)
                    continue

                if to_info.scheme != "local":
                    raise NotImplementedError

                msg = "Downloading '{}' to '{}'".format(from_info, to_info)
                logger.debug(msg)

                tmp_file = tmp_fname(to_info)
                if not name:
                    name = to_info.name

                if not no_progress_bar:
                    # real progress is not always available,
                    # lets at least show start and finish
                    progress.update_target(name, 0, None)

                makedirs(fspath_py35(to_info.parent), exist_ok=True)

                try:
                    self._download(
                        from_info,
                        tmp_file,
                        name=name,
                        ctx=ctx,
                        resume=resume,
                        no_progress_bar=no_progress_bar,
                    )
                except Exception:
                    fails += 1
                    msg = "failed to download '{}' to '{}'"
                    logger.exception(msg.format(from_info, to_info))
                    continue

                move(tmp_file, fspath_py35(to_info))

                if not no_progress_bar:
                    progress.finish_target(name)

        return fails

    def remove(self, path_info):
        raise RemoteActionNotImplemented("remove", self.scheme)

    def move(self, from_info, to_info):
        self.copy(from_info, to_info)
        self.remove(from_info)

    def copy(self, from_info, to_info, ctx=None):
        raise RemoteActionNotImplemented("copy", self.scheme)

    def exists(self, path_info):
        raise NotImplementedError

    @classmethod
    def _verify_path_args(cls, from_infos, to_infos, names=None):
        assert isinstance(from_infos, list)
        assert isinstance(to_infos, list)
        assert len(from_infos) == len(to_infos)

        if not names:
            names = len(to_infos) * [None]
        else:
            assert isinstance(names, list)
            assert len(names) == len(to_infos)

        return names

    def path_to_checksum(self, path):
        return "".join(self.path_cls(path).parts[-2:])

    def checksum_to_path_info(self, checksum):
        return self.path_info / checksum[0:2] / checksum[2:]

    def list_cache_paths(self):
        raise NotImplementedError

    def all(self):
        # NOTE: The list might be way too big(e.g. 100M entries, md5 for each
        # is 32 bytes, so ~3200Mb list) and we don't really need all of it at
        # the same time, so it makes sense to use a generator to gradually
        # iterate over it, without keeping all of it in memory.
        return (
            self.path_to_checksum(path) for path in self.list_cache_paths()
        )

    def gc(self, cinfos):
        used = self.extract_used_local_checksums(cinfos)

        if self.scheme != "":
            used |= {
                info[self.PARAM_CHECKSUM]
                for info in cinfos.get(self.scheme, [])
            }

        removed = False
        for checksum in self.all():
            if checksum in used:
                continue
            path_info = self.checksum_to_path_info(checksum)
            self.remove(path_info)
            removed = True
        return removed

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
        actual = self.get_checksum(cache_info)

        logger.debug(
            "cache '{}' expected '{}' actual '{}'".format(
                str(cache_info), checksum, actual
            )
        )

        if not checksum or not actual:
            return True

        if actual.split(".")[0] == checksum.split(".")[0]:
            return False

        if self.exists(cache_info):
            logger.warning("corrupted cache file '{}'.".format(cache_info))
            self.remove(cache_info)

        return True

    def _changed_dir_cache(self, checksum):
        if self.changed_cache_file(checksum):
            return True

        if not self._changed_unpacked_dir(checksum):
            return False

        for entry in self.get_dir_cache(checksum):
            entry_checksum = entry[self.PARAM_CHECKSUM]
            if self.changed_cache_file(entry_checksum):
                return True

        self._update_unpacked_dir(checksum)
        return False

    def changed_cache(self, checksum):
        if self.is_dir_checksum(checksum):
            return self._changed_dir_cache(checksum)
        return self.changed_cache_file(checksum)

    def cache_exists(self, checksums):
        """Check if the given checksums are stored in the remote.

        There are two ways of performing this check:

        - Traverse: Get a list of all the files in the remote
            (traversing the cache directory) and compare it with
            the given checksums.

        - No traverse: For each given checksum, run the `exists`
            method and filter the checksums that aren't on the remote.
            This is done in parallel threads.
            It also shows a progress bar when performing the check.

        The reason for such an odd logic is that most of the remotes
        take much shorter time to just retrieve everything they have under
        a certain prefix (e.g. s3, gs, ssh, hdfs). Other remotes that can
        check if particular file exists much quicker, use their own
        implementation of cache_exists (see http, local).

        Returns:
            A list with checksums that were found in the remote
        """
        progress_callback = ProgressCallback(len(checksums))

        def exists_with_progress(chunks):
            return self.batch_exists(chunks, callback=progress_callback)

        if self.no_traverse and hasattr(self, "batch_exists"):
            with ThreadPoolExecutor(max_workers=self.JOBS) as executor:
                path_infos = [self.checksum_to_path_info(x) for x in checksums]
                chunks = to_chunks(path_infos, num_chunks=self.JOBS)
                results = executor.map(exists_with_progress, chunks)
                in_remote = itertools.chain.from_iterable(results)
                ret = list(itertools.compress(checksums, in_remote))
                progress_callback.finish("")
                return ret

        return list(set(checksums) & set(self.all()))

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
        self, path_info, checksum, force, progress_callback=None
    ):
        cache_info = self.checksum_to_path_info(checksum)
        if self.exists(path_info):
            msg = "data '{}' exists. Removing before checkout."
            logger.warning(msg.format(str(path_info)))
            self.safe_remove(path_info, force=force)

        self.link(cache_info, path_info)
        self.state.save_link(path_info)
        self.state.save(path_info, checksum)
        if progress_callback:
            progress_callback.update(str(path_info))

    def makedirs(self, path_info):
        raise NotImplementedError

    def _checkout_dir(
        self, path_info, checksum, force, progress_callback=None
    ):
        # Create dir separately so that dir is created
        # even if there are no files in it
        if not self.exists(path_info):
            self.makedirs(path_info)

        dir_info = self.get_dir_cache(checksum)

        logger.debug("Linking directory '{}'.".format(path_info))

        for entry in dir_info:
            relative_path = entry[self.PARAM_RELPATH]
            entry_checksum = entry[self.PARAM_CHECKSUM]
            entry_cache_info = self.checksum_to_path_info(entry_checksum)
            entry_info = path_info / relative_path

            entry_checksum_info = {self.PARAM_CHECKSUM: entry_checksum}
            if self.changed(entry_info, entry_checksum_info):
                if self.exists(entry_info):
                    self.safe_remove(entry_info, force=force)
                self.link(entry_cache_info, entry_info)
                self.state.save(entry_info, entry_checksum)
            if progress_callback:
                progress_callback.update(str(entry_info))

        self._remove_redundant_files(path_info, dir_info, force)

        self.state.save_link(path_info)
        self.state.save(path_info, checksum)

    def _remove_redundant_files(self, path_info, dir_info, force):
        existing_files = set(
            path_info / root / fname
            for root, _, files in self.walk(path_info)
            for fname in files
        )

        needed_files = {
            path_info / entry[self.PARAM_RELPATH] for entry in dir_info
        }

        for path in existing_files - needed_files:
            self.safe_remove(path, force)

    def checkout(
        self, path_info, checksum_info, force=False, progress_callback=None
    ):
        if path_info.scheme not in ["local", self.scheme]:
            raise NotImplementedError

        checksum = checksum_info.get(self.PARAM_CHECKSUM)
        if not checksum:
            logger.warning(
                "No checksum info found for '{}'. "
                "It won't be created.".format(str(path_info))
            )
            self.safe_remove(path_info, force=force)
            return

        if not self.changed(path_info, checksum_info):
            msg = "Data '{}' didn't change."
            logger.debug(msg.format(str(path_info)))
            return

        if self.changed_cache(checksum):
            msg = "Cache '{}' not found. File '{}' won't be created."
            logger.warning(msg.format(checksum, str(path_info)))
            self.safe_remove(path_info, force=force)
            return

        msg = "Checking out '{}' with cache '{}'."
        logger.debug(msg.format(str(path_info), checksum))

        return self._checkout(path_info, checksum, force, progress_callback)

    def _checkout(
        self, path_info, checksum, force=False, progress_callback=None
    ):
        if not self.is_dir_checksum(checksum):
            return self._checkout_file(
                path_info, checksum, force, progress_callback=progress_callback
            )
        return self._checkout_dir(
            path_info, checksum, force, progress_callback=progress_callback
        )

    @staticmethod
    def unprotect(path_info):
        pass

    def _get_unpacked_dir_names(self, checksums):
        return set()

    def extract_used_local_checksums(self, cinfos):
        from dvc.remote import RemoteLOCAL

        used = {info[RemoteLOCAL.PARAM_CHECKSUM] for info in cinfos["local"]}
        unpacked = self._get_unpacked_dir_names(used)
        return used | unpacked

    def _changed_unpacked_dir(self, checksum):
        return True

    def _update_unpacked_dir(self, checksum, progress_callback=None):
        pass
