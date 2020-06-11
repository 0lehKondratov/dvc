import logging
import os
from contextlib import contextmanager

import yaml
from funcy import first
from voluptuous import Invalid

from dvc.remote.local import _log_exceptions
from dvc.schema import COMPILED_LOCK_FILE_STAGE_SCHEMA
from dvc.utils import dict_sha256, relpath
from dvc.utils.fs import makedirs
from dvc.utils.yaml import dump_yaml

from .loader import StageLoader
from .serialize import to_single_stage_lockfile

logger = logging.getLogger(__name__)


def _get_cache_hash(cache, key=False):
    if key:
        cache["outs"] = [out["path"] for out in cache.get("outs", [])]
    return dict_sha256(cache)


def _get_stage_hash(stage):
    if not (stage.cmd and stage.deps and stage.outs):
        return None

    for dep in stage.deps:
        if not (dep.scheme == "local" and dep.def_path and dep.get_checksum()):
            return None

    for out in stage.outs:
        if out.scheme != "local" or not out.def_path or out.persist:
            return None

    return _get_cache_hash(to_single_stage_lockfile(stage), key=True)


class StageCache:
    def __init__(self, repo):
        self.repo = repo
        self.cache_dir = os.path.join(repo.cache.local.cache_dir, "runs")

    def _get_cache_dir(self, key):
        return os.path.join(self.cache_dir, key[:2], key)

    def _get_cache_path(self, key, value):
        return os.path.join(self._get_cache_dir(key), value)

    def _load_cache(self, key, value):
        path = self._get_cache_path(key, value)

        try:
            with open(path) as fobj:
                return COMPILED_LOCK_FILE_STAGE_SCHEMA(yaml.safe_load(fobj))
        except FileNotFoundError:
            return None
        except (yaml.error.YAMLError, Invalid):
            logger.warning("corrupted cache file '%s'.", relpath(path))
            os.unlink(path)
            return None

    def _load(self, stage):
        key = _get_stage_hash(stage)
        if not key:
            return None

        cache_dir = self._get_cache_dir(key)
        if not os.path.exists(cache_dir):
            return None

        for value in os.listdir(cache_dir):
            cache = self._load_cache(key, value)
            if cache:
                return cache

        return None

    def _create_stage(self, cache, wdir=None):
        from . import create_stage, PipelineStage

        stage = create_stage(
            PipelineStage,
            repo=self.repo,
            path="dvc.yaml",
            cmd=cache["cmd"],
            wdir=wdir,
            outs=[out["path"] for out in cache["outs"]],
            external=True,
        )
        StageLoader.fill_from_lock(stage, cache)
        return stage

    @contextmanager
    def _cache_type_copy(self):
        cache_types = self.repo.cache.local.cache_types
        self.repo.cache.local.cache_types = ["copy"]
        try:
            yield
        finally:
            self.repo.cache.local.cache_types = cache_types

    def _uncached_outs(self, stage, cache):
        # NOTE: using temporary stage to avoid accidentally modifying original
        # stage and to workaround `commit/checkout` not working for uncached
        # outputs.
        cached_stage = self._create_stage(cache, wdir=stage.wdir)

        outs_no_cache = [
            out.def_path for out in stage.outs if not out.use_cache
        ]

        # NOTE: using copy link to make it look like a git-tracked file
        with self._cache_type_copy():
            for out in cached_stage.outs:
                if out.def_path in outs_no_cache:
                    yield out

    def save(self, stage):
        cache_key = _get_stage_hash(stage)
        if not cache_key:
            return

        cache = to_single_stage_lockfile(stage)
        cache_value = _get_cache_hash(cache)

        existing_cache = self._load_cache(cache_key, cache_value)
        cache = existing_cache or cache

        for out in self._uncached_outs(stage, cache):
            out.commit()

        if existing_cache:
            return

        # sanity check
        COMPILED_LOCK_FILE_STAGE_SCHEMA(cache)

        path = self._get_cache_path(cache_key, cache_value)
        dpath = os.path.dirname(path)
        makedirs(dpath, exist_ok=True)
        dump_yaml(path, cache)

    def is_cached(self, stage):
        return bool(self._load(stage))

    def restore(self, stage):
        cache = self._load(stage)
        if not cache:
            return
        StageLoader.fill_from_lock(stage, cache)

        for out in self._uncached_outs(stage, cache):
            out.checkout()

    @staticmethod
    def _transfer(func, from_remote, to_remote):
        ret = []

        runs = from_remote.path_info / "runs"
        if not from_remote.exists(runs):
            return []

        for src in from_remote.walk_files(runs):
            rel = src.relative_to(from_remote.path_info)
            dst = to_remote.path_info / rel
            key = dst.parent
            # check if any build cache already exists for this key
            if to_remote.exists(key) and first(to_remote.walk_files(key)):
                continue
            func(src, dst)
            ret.append((src.parent.name, src.name))

        return ret

    def push(self, remote):
        remote = self.repo.cloud.get_remote(remote)
        return self._transfer(
            _log_exceptions(remote.tree.upload, "upload"),
            self.repo.cache.local.tree,
            remote.tree,
        )

    def pull(self, remote):
        remote = self.repo.cloud.get_remote(remote)
        return self._transfer(
            _log_exceptions(remote.tree.download, "download"),
            remote.tree,
            self.repo.cache.local.tree,
        )

    def get_used_cache(self, used_run_cache, *args, **kwargs):
        from dvc.cache import NamedCache

        cache = NamedCache()

        for key, value in used_run_cache:
            entry = self._load_cache(key, value)
            if not entry:
                continue
            stage = self._create_stage(entry)
            cache.update(stage.get_used_cache(*args, **kwargs))
        return cache
