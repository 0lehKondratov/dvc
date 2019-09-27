from __future__ import unicode_literals

import argparse
import logging

from dvc.exceptions import DvcException
from .base import CmdBaseNoRepo, append_doc_link


logger = logging.getLogger(__name__)


class CmdGet(CmdBaseNoRepo):
    def run(self):
        from dvc.repo import Repo

        try:
            Repo.get(
                self.args.url,
                path=self.args.path,
                out=self.args.out,
                rev=self.args.rev,
            )
            return 0
        except DvcException:
            logger.exception(
                "failed to get '{}' from '{}'".format(
                    self.args.path, self.args.url
                )
            )
            return 1


def add_parser(subparsers, parent_parser):
    GET_HELP = "Download data from DVC repository."
    get_parser = subparsers.add_parser(
        "get",
        parents=[parent_parser],
        description=append_doc_link(GET_HELP, "get"),
        help=GET_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    get_parser.add_argument(
        "url", help="URL of Git repository with DVC project to download from."
    )
    get_parser.add_argument("path", help="Path to data within DVC repository.")
    get_parser.add_argument(
        "-o", "--out", nargs="?", help="Destination path to put data to."
    )
    get_parser.add_argument(
        "--rev", nargs="?", help="DVC repository git revision."
    )
    get_parser.set_defaults(func=CmdGet)
