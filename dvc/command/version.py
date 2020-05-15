import argparse
import itertools
import logging
import os
import pathlib
import platform
import uuid

from dvc.command.base import CmdBaseNoRepo, append_doc_link
from dvc.exceptions import DvcException, NotDvcRepoError
from dvc.scm.base import SCMError
from dvc.system import System
from dvc.utils import is_binary, relpath
from dvc.utils.pkg import PKG
from dvc.version import __version__

try:
    import psutil
except ImportError:
    psutil = None


logger = logging.getLogger(__name__)


class CmdVersion(CmdBaseNoRepo):
    def run(self):
        from dvc.repo import Repo

        info = [
            f"DVC version: {__version__}",
            f"Python version: {platform.python_version()}",
            f"Platform: {platform.platform()}",
            f"Binary: {is_binary()}",
            f"Package: {PKG}",
            f"Supported remotes: {self.get_supported_remotes()}",
        ]

        try:
            repo = Repo()
            root_directory = repo.root_dir

            # cache_dir might not exist yet (e.g. after `dvc init`), and we
            # can't auto-create it, as it might cause issues if the user
            # later decides to enable shared cache mode with
            # `dvc config cache.shared group`.
            if os.path.exists(repo.cache.local.cache_dir):
                info.append(
                    "Cache: {}".format(self.get_linktype_support_info(repo))
                )
                if psutil:
                    fs_type = self.get_fs_type(repo.cache.local.cache_dir)
                    info.append(
                        f"Filesystem type (cache directory): {fs_type}"
                    )
            else:
                logger.warning(
                    "Unable to detect supported link types, as cache "
                    "directory '{}' doesn't exist. It is usually auto-created "
                    "by commands such as `dvc add/fetch/pull/run/import`, "
                    "but you could create it manually to enable this "
                    "check.".format(relpath(repo.cache.local.cache_dir))
                )

        except NotDvcRepoError:
            root_directory = os.getcwd()
        except SCMError:
            root_directory = os.getcwd()
            info.append("Repo: dvc, git (broken)")
        else:
            info.append("Repo: {}".format(_get_dvc_repo_info(repo)))

        if psutil:
            fs_root = self.get_fs_type(os.path.abspath(root_directory))
            info.append(f"Filesystem type (workspace): {fs_root}")

        logger.info("\n".join(info))
        return 0

    @staticmethod
    def get_fs_type(path):
        partition = {
            pathlib.Path(part.mountpoint): (part.fstype, part.device)
            for part in psutil.disk_partitions(all=True)
        }

        path = pathlib.Path(path)

        for parent in itertools.chain([path], path.parents):
            if parent in partition:
                return partition[parent]
        return ("unknown", "none")

    @staticmethod
    def get_linktype_support_info(repo):
        links = {
            "reflink": (System.reflink, None),
            "hardlink": (System.hardlink, System.is_hardlink),
            "symlink": (System.symlink, System.is_symlink),
        }

        fname = "." + str(uuid.uuid4())
        src = os.path.join(repo.cache.local.cache_dir, fname)
        open(src, "w").close()
        dst = os.path.join(repo.root_dir, fname)

        cache = []

        for name, (link, is_link) in links.items():
            try:
                link(src, dst)
                status = "supported"
                if is_link and not is_link(dst):
                    status = "broken"
                os.unlink(dst)
            except DvcException:
                status = "not supported"
            cache.append(f"{name} - {status}")
        os.remove(src)

        return ", ".join(cache)

    @staticmethod
    def get_supported_remotes():
        from dvc.remote import REMOTES

        supported_remotes = []
        for remote in REMOTES:
            if not remote.get_missing_deps():
                supported_remotes.append(remote.scheme)

        return ", ".join(supported_remotes)


def _get_dvc_repo_info(repo):
    if repo.config.get("core", {}).get("no_scm", False):
        return "dvc (no_scm)"

    if repo.root_dir != repo.scm.root_dir:
        return "dvc (subdir), git"

    return "dvc, git"


def add_parser(subparsers, parent_parser):
    VERSION_HELP = (
        "Display the DVC version and system/environment information."
    )
    version_parser = subparsers.add_parser(
        "version",
        parents=[parent_parser],
        description=append_doc_link(VERSION_HELP, "version"),
        help=VERSION_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    version_parser.set_defaults(func=CmdVersion)
