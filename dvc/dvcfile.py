import contextlib
import logging
import os

from voluptuous import MultipleInvalid

import dvc.prompt as prompt
from dvc import serialize
from dvc.exceptions import DvcException
from dvc.stage.exceptions import (
    StageFileAlreadyExistsError,
    StageFileBadNameError,
    StageFileDoesNotExistError,
    StageFileFormatError,
    StageFileIsNotDvcFileError,
)
from dvc.stage.loader import SingleStageLoader, StageLoader
from dvc.utils import relpath
from dvc.utils.collections import apply_diff
from dvc.utils.yaml import dump_yaml, parse_yaml, parse_yaml_for_update

logger = logging.getLogger(__name__)

DVC_FILE = "Dvcfile"
DVC_FILE_SUFFIX = ".dvc"
PIPELINE_FILE = "dvc.yaml"
PIPELINE_LOCK = "dvc.lock"


class LockfileCorruptedError(DvcException):
    pass


def is_valid_filename(path):
    return path.endswith(DVC_FILE_SUFFIX) or os.path.basename(path) in [
        DVC_FILE,
        PIPELINE_FILE,
    ]


def is_dvc_file(path):
    return os.path.isfile(path) and (
        is_valid_filename(path) or os.path.basename(path) == PIPELINE_LOCK
    )


def check_dvc_filename(path):
    if not is_valid_filename(path):
        raise StageFileBadNameError(
            "bad DVC-file name '{}'. DVC-files should be named "
            "'Dvcfile' or have a '.dvc' suffix (e.g. '{}.dvc').".format(
                relpath(path), os.path.basename(path)
            )
        )


class FileMixin:
    SCHEMA = None

    def __init__(self, repo, path, **kwargs):
        self.repo = repo
        self.path = path

    def __repr__(self):
        return "{}: {}".format(
            self.__class__.__name__, relpath(self.path, self.repo.root_dir)
        )

    def __hash__(self):
        return hash(self.path)

    def __eq__(self, other):
        return self.repo == other.repo and os.path.abspath(
            self.path
        ) == os.path.abspath(other.path)

    def __str__(self):
        return f"{self.__class__.__name__}: {self.relpath}"

    @property
    def relpath(self):
        return relpath(self.path)

    def exists(self):
        return self.repo.tree.exists(self.path)

    def _load(self):
        # it raises the proper exceptions by priority:
        # 1. when the file doesn't exists
        # 2. filename is not a DVC-file
        # 3. path doesn't represent a regular file
        if not self.exists():
            raise StageFileDoesNotExistError(self.path)
        check_dvc_filename(self.path)
        if not self.repo.tree.isfile(self.path):
            raise StageFileIsNotDvcFileError(self.path)

        with self.repo.tree.open(self.path) as fd:
            stage_text = fd.read()
        d = parse_yaml(stage_text, self.path)
        self.validate(d, self.relpath)
        return d, stage_text

    @classmethod
    def validate(cls, d, fname=None):
        assert cls.SCHEMA
        try:
            cls.SCHEMA(d)
        except MultipleInvalid as exc:
            raise StageFileFormatError(f"'{fname}' format error: {exc}")

    def remove_with_prompt(self, force=False):
        raise NotImplementedError

    def remove(self, force=False):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.path)

    def dump(self, stage, **kwargs):
        raise NotImplementedError


class SingleStageFile(FileMixin):
    from dvc.schema import COMPILED_SINGLE_STAGE_SCHEMA as SCHEMA

    def __init__(self, repo, path):
        super().__init__(repo, path)

    @property
    def stage(self):
        data, raw = self._load()
        return SingleStageLoader.load_stage(self, data, raw)

    @property
    def stages(self):
        data, raw = self._load()
        return SingleStageLoader(self, data, raw)

    def dump(self, stage, **kwargs):
        """Dumps given stage appropriately in the dvcfile."""
        from dvc.stage import PipelineStage

        assert not isinstance(stage, PipelineStage)
        check_dvc_filename(self.path)
        logger.debug(
            "Saving information to '{file}'.".format(file=relpath(self.path))
        )
        dump_yaml(self.path, serialize.to_single_stage_file(stage))
        self.repo.scm.track_file(self.relpath)

    def remove_with_prompt(self, force=False):
        if not self.exists():
            return

        msg = (
            "'{}' already exists. Do you wish to run the command and "
            "overwrite it?".format(relpath(self.path))
        )
        if not (force or prompt.confirm(msg)):
            raise StageFileAlreadyExistsError(self.path)

        self.remove()

    def remove_stage(self, stage):
        self.remove()


class PipelineFile(FileMixin):
    """Abstraction for pipelines file, .yaml + .lock combined."""

    from dvc.schema import COMPILED_MULTI_STAGE_SCHEMA as SCHEMA

    @property
    def _lockfile(self):
        return Lockfile(self.repo, os.path.splitext(self.path)[0] + ".lock")

    def dump(self, stage, update_pipeline=False, no_lock=False):
        """Dumps given stage appropriately in the dvcfile."""
        from dvc.stage import PipelineStage

        assert isinstance(stage, PipelineStage)
        check_dvc_filename(self.path)

        if update_pipeline and not stage.is_data_source:
            self._dump_pipeline_file(stage)

        if not no_lock:
            self._dump_lockfile(stage)

    def _dump_lockfile(self, stage):
        self._lockfile.dump(stage)

    def _dump_pipeline_file(self, stage):
        data = {}
        if self.exists():
            with open(self.path) as fd:
                data = parse_yaml_for_update(fd.read(), self.path)
        else:
            logger.info("Creating '%s'", self.relpath)
            open(self.path, "w+").close()

        data["stages"] = data.get("stages", {})
        stage_data = serialize.to_pipeline_file(stage)
        if data["stages"].get(stage.name):
            orig_stage_data = data["stages"][stage.name]
            apply_diff(stage_data[stage.name], orig_stage_data)
        else:
            data["stages"].update(stage_data)

        logger.info(
            "Adding stage '%s' to '%s'", stage.name, self.relpath,
        )
        dump_yaml(self.path, data)
        self.repo.scm.track_file(self.relpath)

    @property
    def stage(self):
        raise DvcException(
            "PipelineFile has multiple stages. Please specify it's name."
        )

    @property
    def stages(self):
        data, _ = self._load()
        lockfile_data = self._lockfile.load()
        return StageLoader(self, data.get("stages", {}), lockfile_data)

    def remove(self, force=False):
        if not force:
            logger.warning("Cannot remove pipeline file.")
            return

        super().remove()
        self._lockfile.remove()

    def remove_stage(self, stage):
        self._lockfile.remove_stage(stage)
        if not self.exists():
            return

        with open(self.path, "r") as f:
            d = parse_yaml_for_update(f.read(), self.path)

        self.validate(d, self.path)
        if stage.name not in d.get("stages", {}):
            return

        logger.debug("Removing '%s' from '%s'", stage.name, self.path)
        del d["stages"][stage.name]
        dump_yaml(self.path, d)


class Lockfile(FileMixin):
    from dvc.schema import COMPILED_LOCKFILE_SCHEMA as SCHEMA

    def load(self):
        if not self.exists():
            return {}
        with self.repo.tree.open(self.path) as fd:
            data = parse_yaml(fd.read(), self.path)
        try:
            self.validate(data, fname=self.relpath)
        except StageFileFormatError:
            raise LockfileCorruptedError(
                f"Lockfile '{self.relpath}' is corrupted."
            )
        return data

    def dump(self, stage, **kwargs):
        stage_data = serialize.to_lockfile(stage)
        if not self.exists():
            modified = True
            logger.info("Generating lock file '%s'", self.relpath)
            data = stage_data
            open(self.path, "w+").close()
        else:
            with self.repo.tree.open(self.path, "r") as fd:
                data = parse_yaml_for_update(fd.read(), self.path)
            modified = data.get(stage.name, {}) != stage_data.get(
                stage.name, {}
            )
            if modified:
                logger.info("Updating lock file '%s'", self.relpath)
            data.update(stage_data)
        dump_yaml(self.path, data)
        if modified:
            self.repo.scm.track_file(self.relpath)

    def remove_stage(self, stage):
        if not self.exists():
            return

        with open(self.path) as f:
            d = parse_yaml_for_update(f.read(), self.path)
        self.validate(d, self.path)

        if stage.name not in d:
            return

        logger.debug("Removing '%s' from '%s'", stage.name, self.path)
        del d[stage.name]

        dump_yaml(self.path, d)


class Dvcfile:
    def __new__(cls, repo, path, **kwargs):
        assert path
        assert repo

        _, ext = os.path.splitext(path)
        if ext in [".yaml", ".yml"]:
            return PipelineFile(repo, path, **kwargs)
        # fallback to single stage file for better error messages
        return SingleStageFile(repo, path, **kwargs)
