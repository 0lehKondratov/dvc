import os


def run(
    self,
    cmd=None,
    deps=None,
    outs=None,
    outs_no_cache=None,
    metrics=None,
    metrics_no_cache=None,
    fname=None,
    cwd=os.curdir,
    no_exec=False,
    overwrite=False,
    ignore_build_cache=False,
    remove_outs=False,
):
    from dvc.stage import Stage

    if outs is None:
        outs = []
    if deps is None:
        deps = []
    if outs_no_cache is None:
        outs_no_cache = []
    if metrics is None:
        metrics = []
    if metrics_no_cache is None:
        metrics_no_cache = []

    with self.state:
        stage = Stage.create(
            repo=self,
            fname=fname,
            cmd=cmd,
            cwd=cwd,
            outs=outs,
            outs_no_cache=outs_no_cache,
            metrics=metrics,
            metrics_no_cache=metrics_no_cache,
            deps=deps,
            overwrite=overwrite,
            ignore_build_cache=ignore_build_cache,
            remove_outs=remove_outs,
        )

    if stage is None:
        return None

    self.check_dag(self.stages() + [stage])

    self.files_to_git_add = []
    with self.state:
        if not no_exec:
            stage.run()

    stage.dump()

    self.remind_to_git_add()

    return stage
