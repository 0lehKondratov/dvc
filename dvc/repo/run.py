import os

from funcy import concat, first, lfilter

from dvc.exceptions import InvalidArgumentError
from dvc.stage.exceptions import (
    DuplicateStageName,
    InvalidStageName,
    StageFileAlreadyExistsError,
)

from ..exceptions import OutputDuplicationError
from . import locked
from .scm_context import scm_context


def is_valid_name(name: str):
    from ..stage import INVALID_STAGENAME_CHARS

    return not INVALID_STAGENAME_CHARS & set(name)


def parse_params(path_params):
    ret = []
    for path_param in path_params:
        path, _, params_str = path_param.rpartition(":")
        # remove empty strings from params, on condition such as `-p "file1:"`
        params = lfilter(bool, params_str.split(","))
        if not path:
            ret.extend(params)
        else:
            ret.append({path: params})
    return ret


def _get_file_path(kwargs):
    from dvc.dvcfile import DVC_FILE_SUFFIX, DVC_FILE

    out = first(
        concat(
            kwargs.get("outs", []),
            kwargs.get("outs_no_cache", []),
            kwargs.get("metrics", []),
            kwargs.get("metrics_no_cache", []),
            kwargs.get("plots", []),
            kwargs.get("plots_no_cache", []),
            kwargs.get("outs_persist", []),
            kwargs.get("outs_persist_no_cache", []),
        )
    )

    return (
        os.path.basename(os.path.normpath(out)) + DVC_FILE_SUFFIX
        if out
        else DVC_FILE
    )


@locked
@scm_context
def run(self, fname=None, no_exec=False, single_stage=False, **kwargs):
    from dvc.stage import PipelineStage, Stage, create_stage
    from dvc.dvcfile import Dvcfile, PIPELINE_FILE

    if not kwargs.get("cmd"):
        raise InvalidArgumentError("command is not specified")

    stage_cls = PipelineStage
    path = PIPELINE_FILE
    stage_name = kwargs.get("name")

    if stage_name and single_stage:
        raise InvalidArgumentError(
            "`-n|--name` is incompatible with `--single-stage`"
        )

    if not stage_name and not single_stage:
        raise InvalidArgumentError("`-n|--name` is required")

    if single_stage:
        kwargs.pop("name", None)
        stage_cls = Stage
        path = fname or _get_file_path(kwargs)
    else:
        if not is_valid_name(stage_name):
            raise InvalidStageName

    params = parse_params(kwargs.pop("params", []))
    stage = create_stage(
        stage_cls, repo=self, path=path, params=params, **kwargs
    )
    if stage is None:
        return None

    dvcfile = Dvcfile(self, stage.path)
    if dvcfile.exists():
        if kwargs.get("overwrite", True):
            dvcfile.remove_stage(stage)
        elif stage_cls != PipelineStage:
            raise StageFileAlreadyExistsError(dvcfile.relpath)
        elif stage_name and stage_name in dvcfile.stages:
            raise DuplicateStageName(stage_name, dvcfile)

    try:
        self.check_modified_graph([stage])
    except OutputDuplicationError as exc:
        raise OutputDuplicationError(exc.output, set(exc.stages) - {stage})

    if no_exec:
        stage.ignore_outs()
    else:
        stage.run(
            no_commit=kwargs.get("no_commit", False),
            run_cache=kwargs.get("run_cache", True),
        )

    dvcfile.dump(stage, update_pipeline=True, no_lock=no_exec)
    return stage
