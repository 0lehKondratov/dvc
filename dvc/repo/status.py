import logging
from itertools import compress

from funcy.py3 import cat

from dvc.exceptions import InvalidArgumentError

from . import locked

logger = logging.getLogger(__name__)


def _joint_status(stages):
    status = {}

    for stage in stages:
        if stage.frozen and not stage.is_repo_import:
            logger.warning(
                "{} is frozen. Its dependencies are"
                " not going to be shown in the status output.".format(stage)
            )

        status.update(stage.status(check_updates=True))

    return status


def _local_status(self, targets=None, with_deps=False, recursive=False):
    if targets:
        stages = cat(
            self.collect(t, with_deps=with_deps, recursive=recursive)
            for t in targets
        )
    else:
        stages = self.collect(None, with_deps=with_deps, recursive=recursive)

    return _joint_status(stages)


def _cloud_status(
    self,
    targets=None,
    jobs=None,
    remote=None,
    all_branches=False,
    with_deps=False,
    all_tags=False,
    recursive=False,
    all_commits=False,
):
    """Returns a dictionary with the files that are new or deleted.

    - new: Remote doesn't have the file
    - deleted: File is no longer in the local cache

    Example:
            Given the following commands:

            $ echo "foo" > foo
            $ echo "bar" > bar
            $ dvc add foo bar
            $ dvc status -c

            It will return something like:

            { "foo": "new", "bar": "new" }

            Now, after pushing and removing "bar" from the local cache:

            $ dvc push
            $ rm .dvc/cache/c1/57a79031e1c40f85931829bc5fc552

            The result would be:

            { "bar": "deleted" }
    """
    import dvc.remote.base as cloud

    used = self.used_cache(
        targets,
        all_branches=all_branches,
        all_tags=all_tags,
        all_commits=all_commits,
        with_deps=with_deps,
        force=True,
        remote=remote,
        jobs=jobs,
        recursive=recursive,
    )

    ret = {}
    status_info = self.cloud.status(used, jobs, remote=remote)
    for info in status_info.values():
        name = info["name"]
        status = info["status"]
        if status in [cloud.STATUS_OK, cloud.STATUS_MISSING]:
            continue

        prefix_map = {cloud.STATUS_DELETED: "deleted", cloud.STATUS_NEW: "new"}

        ret[name] = prefix_map[status]

    return ret


@locked
def status(
    self,
    targets=None,
    jobs=None,
    cloud=False,
    remote=None,
    all_branches=False,
    with_deps=False,
    all_tags=False,
    all_commits=False,
    recursive=False,
):
    if isinstance(targets, str):
        targets = [targets]

    if cloud or remote:
        return _cloud_status(
            self,
            targets,
            jobs,
            all_branches=all_branches,
            with_deps=with_deps,
            remote=remote,
            all_tags=all_tags,
            all_commits=all_commits,
            recursive=True,
        )

    ignored = list(
        compress(
            ["--all-branches", "--all-tags", "--all-commits", "--jobs"],
            [all_branches, all_tags, all_commits, jobs],
        )
    )
    if ignored:
        msg = "The following options are meaningless for local status: {}"
        raise InvalidArgumentError(msg.format(", ".join(ignored)))

    return _local_status(
        self, targets, with_deps=with_deps, recursive=recursive
    )
