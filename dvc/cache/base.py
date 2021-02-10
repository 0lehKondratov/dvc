import itertools
import logging
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from typing import Optional

from funcy import decorator

from dvc.progress import Tqdm

from ..objects import HashFile, ObjectFormatError

logger = logging.getLogger(__name__)


@decorator
def use_state(call):
    tree = call._args[0].tree  # pylint: disable=protected-access
    with tree.state:
        return call()


class CloudCache:

    DEFAULT_VERIFY = False
    DEFAULT_CACHE_TYPES = ["copy"]
    CACHE_MODE: Optional[int] = None

    def __init__(self, tree):
        self.tree = tree
        self.repo = tree.repo

        self.verify = tree.config.get("verify", self.DEFAULT_VERIFY)
        self.cache_types = tree.config.get("type") or copy(
            self.DEFAULT_CACHE_TYPES
        )
        self.cache_type_confirmed = False

    def move(self, from_info, to_info):
        self.tree.move(from_info, to_info)

    def makedirs(self, path_info):
        self.tree.makedirs(path_info)

    def get(self, hash_info):
        """ get raw object """
        return HashFile(
            # Prefer string path over PathInfo when possible due to performance
            self.hash_to_path(hash_info.value),
            self.tree,
            hash_info,
        )

    @use_state
    def add(self, path_info, tree, hash_info, **kwargs):
        try:
            self.check(hash_info)
            return
        except (ObjectFormatError, FileNotFoundError):
            pass

        cache_info = self.tree.hash_to_path_info(hash_info.value)
        # using our makedirs to create dirs with proper permissions
        self.makedirs(cache_info.parent)
        if isinstance(tree, type(self.tree)):
            self.tree.move(path_info, cache_info)
        else:
            with tree.open(path_info, mode="rb") as fobj:
                self.tree.upload_fobj(fobj, cache_info)
        self.protect(cache_info)
        self.tree.state.save(cache_info, hash_info)

        callback = kwargs.get("download_callback")
        if callback:
            callback(1)

    # Override to return path as a string instead of PathInfo for clouds
    # which support string paths (see local)
    def hash_to_path(self, hash_):
        return self.tree.hash_to_path_info(hash_)

    def protect(self, path_info):  # pylint: disable=unused-argument
        pass

    def is_protected(self, path_info):  # pylint: disable=unused-argument
        return False

    def unprotect(self, path_info):  # pylint: disable=unused-argument
        pass

    def set_exec(self, path_info):  # pylint: disable=unused-argument
        pass

    def check(self, hash_info):
        """Compare the given hash with the (corresponding) actual one.

        - Use `State` as a cache for computed hashes
            + The entries are invalidated by taking into account the following:
                * mtime
                * inode
                * size
                * hash

        - Remove the file from cache if it doesn't match the actual hash
        """

        obj = self.get(hash_info)
        if self.is_protected(obj.path_info):
            logger.trace(
                "Assuming '%s' is unchanged since it is read-only",
                obj.path_info,
            )
            return

        try:
            obj.check(self)
        except ObjectFormatError:
            logger.warning("corrupted cache file '%s'.", obj.path_info)
            self.tree.remove(obj.path_info)
            raise

        # making cache file read-only so we don't need to check it
        # next time
        self.protect(obj.path_info)

    def _list_paths(self, prefix=None, progress_callback=None):
        if prefix:
            if len(prefix) > 2:
                path_info = self.tree.path_info / prefix[:2] / prefix[2:]
            else:
                path_info = self.tree.path_info / prefix[:2]
            prefix = True
        else:
            path_info = self.tree.path_info
            prefix = False
        if progress_callback:
            for file_info in self.tree.walk_files(path_info, prefix=prefix):
                progress_callback()
                yield file_info.path
        else:
            yield from self.tree.walk_files(path_info, prefix=prefix)

    def _path_to_hash(self, path):
        parts = self.tree.PATH_CLS(path).parts[-2:]

        if not (len(parts) == 2 and parts[0] and len(parts[0]) == 2):
            raise ValueError(f"Bad cache file path '{path}'")

        return "".join(parts)

    def list_hashes(self, prefix=None, progress_callback=None):
        """Iterate over hashes in this tree.

        If `prefix` is specified, only hashes which begin with `prefix`
        will be returned.
        """
        for path in self._list_paths(prefix, progress_callback):
            try:
                yield self._path_to_hash(path)
            except ValueError:
                logger.debug(
                    "'%s' doesn't look like a cache file, skipping", path
                )

    def _hashes_with_limit(self, limit, prefix=None, progress_callback=None):
        count = 0
        for hash_ in self.list_hashes(prefix, progress_callback):
            yield hash_
            count += 1
            if count > limit:
                logger.debug(
                    "`list_hashes()` returned max '{}' hashes, "
                    "skipping remaining results".format(limit)
                )
                return

    def _max_estimation_size(self, hashes):
        # Max remote size allowed for us to use traverse method
        return max(
            self.tree.TRAVERSE_THRESHOLD_SIZE,
            len(hashes)
            / self.tree.TRAVERSE_WEIGHT_MULTIPLIER
            * self.tree.LIST_OBJECT_PAGE_SIZE,
        )

    def _estimate_remote_size(self, hashes=None, name=None):
        """Estimate tree size based on number of entries beginning with
        "00..." prefix.
        """
        prefix = "0" * self.tree.TRAVERSE_PREFIX_LEN
        total_prefixes = pow(16, self.tree.TRAVERSE_PREFIX_LEN)
        if hashes:
            max_hashes = self._max_estimation_size(hashes)
        else:
            max_hashes = None

        with Tqdm(
            desc="Estimating size of "
            + (f"cache in '{name}'" if name else "remote cache"),
            unit="file",
        ) as pbar:

            def update(n=1):
                pbar.update(n * total_prefixes)

            if max_hashes:
                hashes = self._hashes_with_limit(
                    max_hashes / total_prefixes, prefix, update
                )
            else:
                hashes = self.list_hashes(prefix, update)

            remote_hashes = set(hashes)
            if remote_hashes:
                remote_size = total_prefixes * len(remote_hashes)
            else:
                remote_size = total_prefixes
            logger.debug(f"Estimated remote size: {remote_size} files")
        return remote_size, remote_hashes

    def list_hashes_traverse(
        self, remote_size, remote_hashes, jobs=None, name=None
    ):
        """Iterate over all hashes found in this tree.
        Hashes are fetched in parallel according to prefix, except in
        cases where the remote size is very small.

        All hashes from the remote (including any from the size
        estimation step passed via the `remote_hashes` argument) will be
        returned.

        NOTE: For large remotes the list of hashes will be very
        big(e.g. 100M entries, md5 for each is 32 bytes, so ~3200Mb list)
        and we don't really need all of it at the same time, so it makes
        sense to use a generator to gradually iterate over it, without
        keeping all of it in memory.
        """
        num_pages = remote_size / self.tree.LIST_OBJECT_PAGE_SIZE
        if num_pages < 256 / self.tree.JOBS:
            # Fetching prefixes in parallel requires at least 255 more
            # requests, for small enough remotes it will be faster to fetch
            # entire cache without splitting it into prefixes.
            #
            # NOTE: this ends up re-fetching hashes that were already
            # fetched during remote size estimation
            traverse_prefixes = [None]
            initial = 0
        else:
            yield from remote_hashes
            initial = len(remote_hashes)
            traverse_prefixes = [f"{i:02x}" for i in range(1, 256)]
            if self.tree.TRAVERSE_PREFIX_LEN > 2:
                traverse_prefixes += [
                    "{0:0{1}x}".format(i, self.tree.TRAVERSE_PREFIX_LEN)
                    for i in range(
                        1, pow(16, self.tree.TRAVERSE_PREFIX_LEN - 2)
                    )
                ]
        with Tqdm(
            desc="Querying "
            + (f"cache in '{name}'" if name else "remote cache"),
            total=remote_size,
            initial=initial,
            unit="file",
        ) as pbar:

            def list_with_update(prefix):
                return list(
                    self.list_hashes(
                        prefix=prefix, progress_callback=pbar.update
                    )
                )

            with ThreadPoolExecutor(
                max_workers=jobs or self.tree.JOBS
            ) as executor:
                in_remote = executor.map(list_with_update, traverse_prefixes,)
                yield from itertools.chain.from_iterable(in_remote)

    def all(self, jobs=None, name=None):
        """Iterate over all hashes in this tree.

        Hashes will be fetched in parallel threads according to prefix
        (except for small remotes) and a progress bar will be displayed.
        """
        logger.debug(
            "Fetching all hashes from '{}'".format(
                name if name else "remote cache"
            )
        )

        if not self.tree.CAN_TRAVERSE:
            return self.list_hashes()

        remote_size, remote_hashes = self._estimate_remote_size(name=name)
        return self.list_hashes_traverse(
            remote_size, remote_hashes, jobs, name
        )

    def _remove_unpacked_dir(self, hash_):
        pass

    def gc(self, used, jobs=None):
        removed = False
        # hashes must be sorted to ensure we always remove .dir files first
        for hash_ in sorted(
            self.all(jobs, str(self.tree.path_info)),
            key=self.tree.is_dir_hash,
            reverse=True,
        ):
            if hash_ in used:
                continue
            path_info = self.tree.hash_to_path_info(hash_)
            if self.tree.is_dir_hash(hash_):
                # backward compatibility
                # pylint: disable=protected-access
                self._remove_unpacked_dir(hash_)
            self.tree.remove(path_info)
            removed = True

        return removed

    def list_hashes_exists(self, hashes, jobs=None, name=None):
        """Return list of the specified hashes which exist in this tree.
        Hashes will be queried individually.
        """
        logger.debug(
            "Querying {} hashes via object_exists".format(len(hashes))
        )
        with Tqdm(
            desc="Querying "
            + ("cache in " + name if name else "remote cache"),
            total=len(hashes),
            unit="file",
        ) as pbar:

            def exists_with_progress(path_info):
                ret = self.tree.exists(path_info)
                pbar.update_msg(str(path_info))
                return ret

            with ThreadPoolExecutor(
                max_workers=jobs or self.tree.JOBS
            ) as executor:
                path_infos = map(self.tree.hash_to_path_info, hashes)
                in_remote = executor.map(exists_with_progress, path_infos)
                ret = list(itertools.compress(hashes, in_remote))
                return ret

    def hashes_exist(self, hashes, jobs=None, name=None):
        """Check if the given hashes are stored in the remote.

        There are two ways of performing this check:

        - Traverse method: Get a list of all the files in the remote
            (traversing the cache directory) and compare it with
            the given hashes. Cache entries will be retrieved in parallel
            threads according to prefix (i.e. entries starting with, "00...",
            "01...", and so on) and a progress bar will be displayed.

        - Exists method: For each given hash, run the `exists`
            method and filter the hashes that aren't on the remote.
            This is done in parallel threads.
            It also shows a progress bar when performing the check.

        The reason for such an odd logic is that most of the remotes
        take much shorter time to just retrieve everything they have under
        a certain prefix (e.g. s3, gs, ssh, hdfs). Other remotes that can
        check if particular file exists much quicker, use their own
        implementation of hashes_exist (see ssh, local).

        Which method to use will be automatically determined after estimating
        the size of the remote cache, and comparing the estimated size with
        len(hashes). To estimate the size of the remote cache, we fetch
        a small subset of cache entries (i.e. entries starting with "00...").
        Based on the number of entries in that subset, the size of the full
        cache can be estimated, since the cache is evenly distributed according
        to hash.

        Returns:
            A list with hashes that were found in the remote
        """
        # Remotes which do not use traverse prefix should override
        # hashes_exist() (see ssh, local)
        assert self.tree.TRAVERSE_PREFIX_LEN >= 2

        hashes = set(hashes)
        if len(hashes) == 1 or not self.tree.CAN_TRAVERSE:
            remote_hashes = self.list_hashes_exists(hashes, jobs, name)
            return remote_hashes

        # Max remote size allowed for us to use traverse method
        remote_size, remote_hashes = self._estimate_remote_size(hashes, name)

        traverse_pages = remote_size / self.tree.LIST_OBJECT_PAGE_SIZE
        # For sufficiently large remotes, traverse must be weighted to account
        # for performance overhead from large lists/sets.
        # From testing with S3, for remotes with 1M+ files, object_exists is
        # faster until len(hashes) is at least 10k~100k
        if remote_size > self.tree.TRAVERSE_THRESHOLD_SIZE:
            traverse_weight = (
                traverse_pages * self.tree.TRAVERSE_WEIGHT_MULTIPLIER
            )
        else:
            traverse_weight = traverse_pages
        if len(hashes) < traverse_weight:
            logger.debug(
                "Large remote ('{}' hashes < '{}' traverse weight), "
                "using object_exists for remaining hashes".format(
                    len(hashes), traverse_weight
                )
            )
            return list(hashes & remote_hashes) + self.list_hashes_exists(
                hashes - remote_hashes, jobs, name
            )

        logger.debug("Querying '{}' hashes via traverse".format(len(hashes)))
        remote_hashes = set(
            self.list_hashes_traverse(remote_size, remote_hashes, jobs, name)
        )
        return list(hashes & set(remote_hashes))
