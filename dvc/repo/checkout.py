from __future__ import unicode_literals

import logging

from dvc.exceptions import CheckoutErrorSuggestGit, CheckoutError
from dvc.progress import Tqdm


logger = logging.getLogger(__name__)


def _cleanup_unused_links(self, all_stages):
    used = [
        out.fspath
        for stage in all_stages
        for out in stage.outs
        if out.scheme == "local"
    ]
    self.state.remove_unused_links(used)


def get_all_files_numbers(stages):
    return sum(stage.get_all_files_number() for stage in stages)


def _checkout(
    self, target=None, with_deps=False, force=False, recursive=False
):
    from dvc.stage import StageFileDoesNotExistError, StageFileBadNameError

    try:
        stages = self.collect(target, with_deps=with_deps, recursive=recursive)
    except (StageFileDoesNotExistError, StageFileBadNameError) as exc:
        if not target:
            raise
        raise CheckoutErrorSuggestGit(target, exc)

    _cleanup_unused_links(self, self.stages)
    total = get_all_files_numbers(stages)
    if total == 0:
        logger.info("Nothing to do")
    failed = []
    with Tqdm(
        total=total, unit="file", desc="Checkout", disable=total == 0
    ) as pbar:
        for stage in stages:
            if stage.locked:
                logger.warning(
                    "DVC-file '{path}' is locked. Its dependencies are"
                    " not going to be checked out.".format(path=stage.relpath)
                )

            failed.extend(
                stage.checkout(force=force, progress_callback=pbar.update_desc)
            )
    if failed:
        raise CheckoutError(failed)
