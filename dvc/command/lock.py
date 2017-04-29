import fasteners
from dvc.command.base import CmdBase
from dvc.logger import Logger
from dvc.runtime import Runtime
from dvc.state_file import StateFile


class CmdLock(CmdBase):
    def __init__(self, settings):
        super(CmdLock, self).__init__(settings)

    def define_args(self, parser):
        self.set_skip_git_actions(parser)

        parser.add_argument('-u', '--unlock', action='store_true', default=False,
                            help='Unlock data item - enable reproduction.')

        parser.add_argument('files', metavar='', help='Data items to lock or unlock.', nargs='*')
        pass

    def run(self):
        if self.is_locker:
            lock = fasteners.InterProcessLock(self.git.lock_file)
            gotten = lock.acquire(timeout=5)
            if not gotten:
                self.warning_dvc_is_busy()
                return 1

        try:

            return self.lock_files(self.parsed_args.files, not self.parsed_args.unlock)
        finally:
            if self.is_locker:
                lock.release()

    def lock_files(self, files, target):
        cmd = 'lock' if target else 'unlock'

        for file in files:
            try:
                data_item = self.settings.path_factory.existing_data_item(file)
                state = StateFile.load(data_item.state.relative, self.settings)

                if state.lock and target:
                    Logger.warn('Data item {} is already locked'.format(data_item.data.relative))
                elif not state.lock and not target:
                    Logger.warn('Data item {} is already unlocked'.format(data_item.data.relative))
                else:
                    state.lock = target
                    Logger.debug('Saving status file for data item {}'.format(data_item.data.relative))
                    state.save()
                    Logger.info('Data item {} was {}ed'.format(data_item.data.relative, cmd))
            except Exception as ex:
                Logger.error('Unable to {} {}: {}'.format(cmd, file, ex))
                raise

        return 0


if __name__ == '__main__':
    Runtime.run(CmdLock, False)
