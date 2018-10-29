import os

from dvc.logger import Logger
from dvc.exceptions import DvcException
from dvc.lock import LockError


class CmdBase(object):
    def __init__(self, args):
        from dvc.project import Project

        self.project = Project(self._find_root())
        self.args = args
        self._set_loglevel(args)

    @staticmethod
    def _set_loglevel(args):
        if args.quiet:
            Logger.be_quiet()

        elif args.verbose:
            Logger.be_verbose()

    def _find_root(self):
        from dvc.project import Project

        root = os.getcwd()
        while True:
            dvc_dir = os.path.join(root, Project.DVC_DIR)
            if os.path.isdir(dvc_dir):
                return root
            if os.path.ismount(root):
                break
            root = os.path.dirname(root)
        msg = "Not a dvc repository (checked up to mount point {})"
        raise DvcException(msg.format(root))

    def run_cmd(self):
        try:
            with self.project.lock:
                return self.run()
        except LockError as ex:
            Logger.error('Failed to lock before running a command', ex)
            return 1

    # Abstract methods that have to be implemented by any inheritance class
    def run(self):
        pass
