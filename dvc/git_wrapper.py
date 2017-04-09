import os

from dvc.logger import Logger
from dvc.config import Config
from dvc.executor import Executor, ExecutorError


class GitWrapperI(object):
    def __init__(self, git_dir=None, commit=None):
        self._git_dir = git_dir
        self._commit = commit

    @property
    def git_dir(self):
        return self._git_dir

    @property
    def lock_file(self):
        return os.path.join(self.git_dir_abs, '.' + Config.CONFIG + '.lock')

    @property
    def git_dir_abs(self):
        return os.path.realpath(self.git_dir)

    @property
    def curr_dir_abs(self):
        return os.path.abspath(os.curdir)

    @property
    def curr_commit(self):
        return self._commit

    def is_ready_to_go(self):
        return True

    @staticmethod
    def git_file_statuses():
        Logger.debug('[Git] Getting file statuses. Command: git status --porcelain')
        code, out, err = Executor.exec_cmd(['git', 'status', '--porcelain'])
        if code != 0:
            raise ExecutorError('[Git] File status command error - {}'.format(err))
        Logger.debug('[Git] Getting file statuses. Success.')

        return GitWrapper.parse_porcelain_files(out)

    @staticmethod
    def parse_porcelain_files(out):
        result = []
        if len(out) > 0:
            lines = out.split('\n')
            for line in lines:
                status = line[:2]
                file = line[3:]
                result.append((status, file))
        return result

    def abs_paths_to_dvc(self, files):
        result = []
        for file in files:
            result.append(os.path.relpath(os.path.abspath(file), self.git_dir_abs))

        return result


class GitWrapper(GitWrapperI):
    def __init__(self):
        super(GitWrapper, self).__init__()

    def is_ready_to_go(self):
        statuses = self.git_file_statuses()
        if len(statuses) > 0:
            Logger.error('[Git] Commit all changed files before running reproducible command.')
            Logger.error('[Git] Changed files:')
            for status, file in statuses:
                Logger.error("{} {}".format(status, file))
            return False

        return True

    @property
    def curr_dir_dvc(self):
        return os.path.relpath(os.path.abspath(os.curdir), self.git_dir_abs)

    @property
    def git_dir(self):
        if self._git_dir:
            return self._git_dir

        try:
            Logger.debug('[Git] Getting git directory. Command: git rev-parse --show-toplevel')
            code, out, err = Executor.exec_cmd(['git', 'rev-parse', '--show-toplevel'])

            if code != 0:
                raise ExecutorError('[Git] Git directory command error - {}'.format(err))
            Logger.debug('[Git] Getting git directory. Success.')

            self._git_dir = out
            return self._git_dir
        except ExecutorError:
            raise
        except Exception as e:
            raise ExecutorError('Unable to run git command: {}'.format(e))
        pass

    @property
    def curr_commit(self):
        Logger.debug('[Git] Getting current git commit. Command: git rev-parse --short HEAD')

        code, out, err = Executor.exec_cmd(['git', 'rev-parse', '--short', 'HEAD'])
        if code != 0:
            raise ExecutorError('[Git] Commit command error - {}'.format(err))
        Logger.debug('[Git] Getting current git commit. Success.')
        return out

    @staticmethod
    def commit_all_changes(message):
        Logger.debug('[Git] Commit all changes. Commands: {} && {} && {}'.format(
            'git add --all', 'git status --porcelain', 'git commit -m'))

        Executor.exec_cmd_only_success(['git', 'add', '--all'])
        out_status = Executor.exec_cmd_only_success(['git', 'status', '--porcelain'])
        Executor.exec_cmd_only_success(['git', 'commit', '-m', message])
        Logger.debug('[Git] Commit all changes. Success.')

        return GitWrapper.parse_porcelain_files(out_status)

    def commit_all_changes_and_log_status(self, message):
        statuses = self.commit_all_changes(message)
        Logger.info('[Git] A new commit {} was made in the current branch. Added files:'.format(
            self.curr_commit))
        for status, file in statuses:
            Logger.info('[Git]\t{} {}'.format(status, file))
        pass

    @staticmethod
    def abs_paths_to_relative(files):
        cur_dir = os.path.realpath(os.curdir)

        result = []
        for file in files:
            result.append(os.path.relpath(os.path.realpath(file), cur_dir))

        return result

    def dvc_paths_to_abs(self, files):
        results = []

        for file in files:
            results.append(os.path.join(self.git_dir_abs, file))

        return results

    def were_files_changed(self, file, code_dependencies, path_factory):
        Logger.debug('[Git] Identify changes. Commands: {} {} && {}'.format(
            'git log -1 --pretty=format:"%h"', file, 'git diff --name-only HEAD *'))

        commit = Executor.exec_cmd_only_success(['git', 'log', '-1', '--pretty=format:"%h"', file])
        commit = commit.strip('"')

        changed_files = Executor.exec_cmd_only_success(['git', 'diff', '--name-only', 'HEAD', commit])
        changed_files = changed_files.strip('"')

        Logger.debug('[Git] Identify changes. Success. Changed files: {}'.format(
            changed_files.replace('\n', ', ')))

        code_files, code_dirs = self.separate_dependency_files_and_dirs(code_dependencies)
        code_files_set = set([path_factory.path(x).dvc for x in code_files])
        for changed_file in changed_files.split('\n'):
            if changed_file in code_files_set:
                return True

            for dir in code_dirs:
                if changed_file.startswith(dir):
                    return True

        return False

    def separate_dependency_files_and_dirs(self, code_dependencies):
        code_files = []
        code_dirs = []

        code_dependencies_abs = self.dvc_paths_to_abs(code_dependencies)
        for code in code_dependencies_abs:
            if os.path.isdir(code):
                code_dirs.append(code)
            else:
                code_files.append(code)

        return code_files, code_dirs
