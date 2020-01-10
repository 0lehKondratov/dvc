import argparse
import logging

from dvc.command.base import append_doc_link
from dvc.command.base import CmdBase
from dvc.exceptions import DvcException


logger = logging.getLogger(__name__)


class CmdImport(CmdBase):
    def run(self):
        try:
            self.repo.imp(
                self.args.url,
                self.args.path,
                out=self.args.out,
                rev=self.args.rev,
            )
        except DvcException:
            logger.exception(
                "failed to import '{}' from '{}'.".format(
                    self.args.path, self.args.url
                )
            )
            return 1
        return 0


def add_parser(subparsers, parent_parser):
    IMPORT_HELP = (
        "Download a file or directory from any DVC project or Git repository and take it under "
        "DVC control."
    )

    import_parser = subparsers.add_parser(
        "import",
        parents=[parent_parser],
        description=append_doc_link(IMPORT_HELP, "import"),
        help=IMPORT_HELP,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    import_parser.add_argument(
        "url",
        help="Location of DVC project or Git repository to download from",
    )
    import_parser.add_argument("path", help="Path to a file or directory within the project or repository")
    import_parser.add_argument(
        "-o", "--out", nargs="?", help="Destination path to download files to"
    )
    import_parser.add_argument(
        "--rev", nargs="?", help="Git revision (e.g. branch, tag, SHA)"
    )
    import_parser.set_defaults(func=CmdImport)
