import argparse
import logging

from dvc.command.base import append_doc_link
from dvc.command.base import CmdBase
from dvc.command.base import fix_subparsers
from dvc.exceptions import DvcException


logger = logging.getLogger(__name__)


def _show_diff(diff):
    from dvc.utils.diff import table

    rows = []
    for fname, mdiff in diff.items():
        for param, change in mdiff.items():
            rows.append([fname, param, change["old"], change["new"]])

    return table(["Path", "Param", "Old", "New"], rows)


class CmdParamsDiff(CmdBase):
    def run(self):
        try:
            diff = self.repo.params.diff(
                a_rev=self.args.a_rev, b_rev=self.args.b_rev,
            )

            if self.args.show_json:
                import json

                logger.info(json.dumps(diff))
            else:
                table = _show_diff(diff)
                if table:
                    logger.info(table)

        except DvcException:
            logger.exception("failed to show params diff")
            return 1

        return 0


def add_parser(subparsers, parent_parser):
    PARAMS_HELP = "Commands to display params."

    params_parser = subparsers.add_parser(
        "params",
        parents=[parent_parser],
        description=append_doc_link(PARAMS_HELP, "params"),
        help=PARAMS_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    params_subparsers = params_parser.add_subparsers(
        dest="cmd",
        help="Use `dvc params CMD --help` to display command-specific help.",
    )

    fix_subparsers(params_subparsers)

    PARAMS_DIFF_HELP = (
        "Show changes in params between commits in the DVC repository, or "
        "between a commit and the workspace."
    )
    params_diff_parser = params_subparsers.add_parser(
        "diff",
        parents=[parent_parser],
        description=append_doc_link(PARAMS_DIFF_HELP, "params/diff"),
        help=PARAMS_DIFF_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    params_diff_parser.add_argument(
        "a_rev", nargs="?", help="Old Git commit to compare (defaults to HEAD)"
    )
    params_diff_parser.add_argument(
        "b_rev",
        nargs="?",
        help=("New Git commit to compare (defaults to the current workspace)"),
    )
    params_diff_parser.add_argument(
        "--show-json",
        action="store_true",
        default=False,
        help="Show output in JSON format.",
    )
    params_diff_parser.set_defaults(func=CmdParamsDiff)
