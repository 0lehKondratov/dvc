from __future__ import unicode_literals

import os

import dvc.logger as logger
from dvc.command.base import CmdBase
from dvc.command.status import CmdDataStatus
from dvc.exceptions import DvcException


class CmdRepro(CmdBase):
    def run(self):
        recursive = not self.args.single_item
        saved_dir = os.path.realpath(os.curdir)
        if self.args.cwd:
            os.chdir(self.args.cwd)

        # Dirty hack so the for loop below can at least enter once
        if self.args.all_pipelines:
            self.args.targets = [None]
        elif not self.args.targets:
            self.args.targets = self.default_targets

        ret = 0
        for target in self.args.targets:
            try:
                stages = self.repo.reproduce(
                    target,
                    recursive=recursive,
                    force=self.args.force,
                    dry=self.args.dry,
                    interactive=self.args.interactive,
                    pipeline=self.args.pipeline,
                    all_pipelines=self.args.all_pipelines,
                    ignore_build_cache=self.args.ignore_build_cache,
                )

                if len(stages) == 0:
                    logger.info(CmdDataStatus.UP_TO_DATE_MSG)

                if self.args.metrics:
                    self.repo.metrics.show()
            except DvcException:
                logger.error()
                ret = 1
                break

        os.chdir(saved_dir)
        return ret


def add_parser(subparsers, parent_parser):
    REPRO_HELP = "Reproduce DVC file. Default file name - 'Dvcfile'."
    repro_parser = subparsers.add_parser(
        "repro",
        parents=[parent_parser],
        description=REPRO_HELP,
        help=REPRO_HELP,
    )
    repro_parser.add_argument(
        "targets", nargs="*", help="DVC file to reproduce."
    )
    repro_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        default=False,
        help="Reproduce even if dependencies were not changed.",
    )
    repro_parser.add_argument(
        "-s",
        "--single-item",
        action="store_true",
        default=False,
        help="Reproduce only single data item without recursive dependencies "
        "check.",
    )
    repro_parser.add_argument(
        "-c",
        "--cwd",
        default=os.path.curdir,
        help="Directory within your repo to reroduce from.",
    )
    repro_parser.add_argument(
        "-m",
        "--metrics",
        action="store_true",
        default=False,
        help="Show metrics after reproduction.",
    )
    repro_parser.add_argument(
        "--dry",
        action="store_true",
        default=False,
        help="Only print the commands that would be executed without "
        "actually executing.",
    )
    repro_parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        default=False,
        help="Ask for confirmation before reproducing each stage.",
    )
    repro_parser.add_argument(
        "-p",
        "--pipeline",
        action="store_true",
        default=False,
        help="Reproduce the whole pipeline that the specified stage file "
        "belongs to.",
    )
    repro_parser.add_argument(
        "-P",
        "--all-pipelines",
        action="store_true",
        default=False,
        help="Reproduce all pipelines in the repo.",
    )
    repro_parser.add_argument(
        "--ignore-build-cache",
        action="store_true",
        default=False,
        help="Reproduce all descendants of a changed stage even if their "
        "direct dependencies didn't change.",
    )
    repro_parser.set_defaults(func=CmdRepro)
