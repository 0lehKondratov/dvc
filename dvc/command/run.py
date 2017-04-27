import os
import fasteners

from dvc.command.base import CmdBase
from dvc.exceptions import DvcException
from dvc.git_wrapper import GitWrapper
from dvc.logger import Logger
from dvc.path.data_item import NotInDataDirError
from dvc.repository_change import RepositoryChange
from dvc.runtime import Runtime
from dvc.state_file import StateFile
from dvc.utils import cached_property


class RunError(DvcException):
    def __init__(self, msg):
        DvcException.__init__(self, 'Run error: {}'.format(msg))


class CmdRun(CmdBase):
    def __init__(self, settings):
        super(CmdRun, self).__init__(settings)

    def define_args(self, parser):
        self.set_skip_git_actions(parser)

        parser.add_argument('--not-repro', help='Not reproducible',
                            action='store_false', default=False)
        parser.add_argument('--stdout', help='output std output to a file')
        parser.add_argument('--stderr', help='output std error to a file')
        parser.add_argument('--input', '-i', action='append',
                            help='Declare input data items for reproducible cmd')
        parser.add_argument('--output', '-o', action='append',
                            help='Declare output data items for reproducible cmd')
        parser.add_argument('--code', '-c', action='append',
                            help='Code dependencies which produce the output')
        parser.add_argument('--shell', help='Shell command', action='store_true', default=False)
        pass

    @property
    def is_reproducible(self):
        return not self.parsed_args.not_repro

    @property
    def code_dependencies(self):
        return self.parsed_args.code or []

    @cached_property
    def declaration_input_data_items(self):
        return self._data_items_from_params(self.parsed_args.input, 'Input')

    @cached_property
    def declaration_output_data_items(self):
        return self._data_items_from_params(self.parsed_args.output, 'Output')

    def run(self):
        if self.is_locker:
            lock = fasteners.InterProcessLock(self.git.lock_file)
            gotten = lock.acquire(timeout=5)
            if not gotten:
                self.warning_dvc_is_busy()
                return 1

        try:
            return self.run_and_commit_if_needed(self.command_args,
                                                 self.data_items_from_args(self.command_args),
                                                 self.parsed_args.stdout,
                                                 self.parsed_args.stderr,
                                                 self.parsed_args.shell)
        finally:
            if self.is_locker:
                lock.release()

    def run_and_commit_if_needed(self, command_args, command_args_data_items,
                                 stdout, stderr, shell, check_if_ready=True):
        if check_if_ready and not self.skip_git_actions and not self.git.is_ready_to_go():
            return 1

        self.run_command(command_args,
                         command_args_data_items,
                         stdout,
                         stderr,
                         shell)

        return self.commit_if_needed('DVC run: {}'.format(' '.join(self.args)))

    def run_command(self, cmd_args, data_items_from_args, stdout=None, stderr=None, shell=False):
        Logger.debug('Run command with args: {}. Data items from args: {}. stdout={}, stderr={}, shell={}'.format(
                     ' '.join(cmd_args),
                     ', '.join([x.data.dvc for x in data_items_from_args]),
                     stdout,
                     stderr,
                     shell))

        repo_change = RepositoryChange(cmd_args, self.settings, stdout, stderr, shell=shell)

        if not self.skip_git_actions and not self._validate_file_states(repo_change):
            self.remove_new_files(repo_change)
            raise RunError('Errors occurred.')

        output_set = set(self.declaration_output_data_items + repo_change.changed_data_items)
        output_files_dvc = [x.data.dvc for x in output_set]

        input_set = set(data_items_from_args + self.declaration_input_data_items) - output_set
        input_files_dvc = [x.data.dvc for x in input_set]

        code_dependencies_dvc = self.git.abs_paths_to_dvc(self.code_dependencies)

        result = []
        for data_item in repo_change.changed_data_items:
            Logger.debug('Move output file "{}" to cache dir "{}" and create a symlink'.format(
                data_item.data.relative, data_item.cache.relative))
            data_item.move_data_to_cache()

            Logger.debug('Create state file "{}"'.format(data_item.state.relative))

            state_file = StateFile(StateFile.COMMAND_RUN,
                                   data_item.state.relative,
                                   self.settings,
                                   input_files_dvc,
                                   output_files_dvc,
                                   code_dependencies_dvc,
                                   argv=cmd_args,
                                   is_reproducible=self.is_reproducible,
                                   stdout=self._stdout_to_dvc(stdout),
                                   stderr=self._stdout_to_dvc(stderr),
                                   shell=shell)
            state_file.save()
            result.append(state_file)

        return result

    def _stdout_to_dvc(self, stdout):
        if stdout in {None, '-'}:
            return stdout
        return self.settings.path_factory.data_item(stdout).data.dvc

    @staticmethod
    def remove_new_files(repo_change):
        for data_item in repo_change.new_data_items:
            Logger.error('Removing created file: {}'.format(data_item.data.relative))
            os.remove(data_item.data.relative)
        pass

    @staticmethod
    def _validate_file_states(repo_change):
        error = False
        for data_item in repo_change.removed_data_items:
            Logger.error('Error: file "{}" was removed'.format(data_item.data.relative))
            error = True

        for file in GitWrapper.abs_paths_to_relative(repo_change.externally_created_files):
            Logger.error('Error: file "{}" was created outside of the data directory'.format(file))
            error = True

        return not error

    def data_items_from_args(self, argv):
        result = []

        for arg in argv:
            try:
                if os.path.isfile(arg):
                    data_item = self.settings.path_factory.data_item(arg)
                    result.append(data_item)
            except NotInDataDirError:
                pass

        return result

    def _data_items_from_params(self, files, param_text):
        if not files:
            return []

        data_items, external = self.settings.path_factory.to_data_items(files)
        if external:
            raise RunError('{} should point to data items from data dir: {}'.format(
                param_text, ', '.join(external))
            )
        return data_items


if __name__ == '__main__':
    Runtime.run(CmdRun)
