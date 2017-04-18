import os
import sys

from dvc.config import Config, ConfigI
from dvc.exceptions import DvcException
from dvc.git_wrapper import GitWrapper
from dvc.logger import Logger
from dvc.settings import Settings
from dvc.system import System


class Runtime(object):
    CONFIG = 'dvc.conf'

    @staticmethod
    def conf_file_path(git_dir):
        return System.realpath(os.path.join(git_dir, Runtime.CONFIG))

    @staticmethod
    def run(cmd_class, parse_config=True, args_start_loc=1):
        """

        Arguments:
            args_start_loc (int): where the arguments this command should use start
        """

        try:
            runtime_git = GitWrapper()

            if parse_config:
                runtime_config = Config(Runtime.conf_file_path(runtime_git.git_dir))

                good = runtime_config.sanity_check()
                if good[0] == False:
                    Logger.error('config \'%s\' is not correctly setup.  Please fix:' % Runtime.CONFIG)
                    for e in good[1]:
                        Logger.error('    ' + e)
                    sys.exit(-1)
            else:
                runtime_config = ConfigI()

            args = sys.argv[args_start_loc:]

            instance = cmd_class(Settings(args, runtime_git, runtime_config))
            sys.exit(instance.run())
        except DvcException as e:
            Logger.error(e)
            sys.exit(1)
