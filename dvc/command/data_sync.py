import argparse

import dvc.logger as logger
from dvc.command.base import CmdBase


class CmdDataBase(CmdBase):
    def do_run(self, target):
        pass

    def run(self):
        if not self.args.targets:
            return self.do_run()

        ret = 0
        for target in self.args.targets:
            if self.do_run(target):
                ret = 1
        return ret


class CmdDataPull(CmdDataBase):
    def do_run(self, target=None):
        try:
            self.project.pull(target=target,
                              jobs=self.args.jobs,
                              remote=self.args.remote,
                              show_checksums=self.args.show_checksums,
                              all_branches=self.args.all_branches,
                              all_tags=self.args.all_tags,
                              with_deps=self.args.with_deps,
                              force=self.args.force,
                              recursive=self.args.recursive)
        except Exception:
            logger.error('failed to pull data from the cloud')
            return 1
        return 0


class CmdDataPush(CmdDataBase):
    def do_run(self, target=None):
        try:
            self.project.push(target=target,
                              jobs=self.args.jobs,
                              remote=self.args.remote,
                              show_checksums=self.args.show_checksums,
                              all_branches=self.args.all_branches,
                              all_tags=self.args.all_tags,
                              with_deps=self.args.with_deps,
                              recursive=self.args.recursive)
        except Exception:
            logger.error('failed to push data to the cloud')
            return 1
        return 0


class CmdDataFetch(CmdDataBase):
    def do_run(self, target=None):
        try:
            self.project.fetch(target=target,
                               jobs=self.args.jobs,
                               remote=self.args.remote,
                               show_checksums=self.args.show_checksums,
                               all_branches=self.args.all_branches,
                               all_tags=self.args.all_tags,
                               with_deps=self.args.with_deps,
                               recursive=self.args.recursive)
        except Exception:
            logger.error('failed to fetch data from the cloud')
            return 1
        return 0


def add_parser(subparsers, parent_parser):
    from dvc.command.status import CmdDataStatus

    # Parent parser used in pull/push/status
    parent_cache_parser = argparse.ArgumentParser(
        add_help=False,
        parents=[parent_parser])
    parent_cache_parser.add_argument(
        '-j',
        '--jobs',
        type=int,
        default=None,
        help='Number of jobs to run simultaneously.')
    parent_cache_parser.add_argument(
        '--show-checksums',
        action='store_true',
        default=False,
        help='Show checksums instead of file names.')
    parent_cache_parser.add_argument(
        'targets',
        nargs='*',
        default=None,
        help='DVC files.')

    # Pull
    PULL_HELP = 'Pull data files from the cloud.'
    pull_parser = subparsers.add_parser(
        'pull',
        parents=[parent_cache_parser],
        description=PULL_HELP,
        help=PULL_HELP)
    pull_parser.add_argument(
        '-r',
        '--remote',
        help='Remote repository to pull from.')
    pull_parser.add_argument(
        '-a',
        '--all-branches',
        action='store_true',
        default=False,
        help='Fetch cache for all branches.')
    pull_parser.add_argument(
        '-T',
        '--all-tags',
        action='store_true',
        default=False,
        help='Fetch cache for all tags.')
    pull_parser.add_argument(
        '-d',
        '--with-deps',
        action='store_true',
        default=False,
        help='Fetch cache for all dependencies of the specified target.')
    pull_parser.add_argument(
        '-f',
        '--force',
        action='store_true',
        default=False,
        help='Do not prompt when removing working directory files.')
    pull_parser.add_argument(
        '-R',
        '--recursive',
        action='store_true',
        default=False,
        help='Pull cache for subdirectories of the specified directory.')
    pull_parser.set_defaults(func=CmdDataPull)

    # Push
    PUSH_HELP = 'Push data files to the cloud.'
    push_parser = subparsers.add_parser(
        'push',
        parents=[parent_cache_parser],
        description=PUSH_HELP,
        help=PUSH_HELP)
    push_parser.add_argument(
        '-r',
        '--remote',
        help='Remote repository to push to.')
    push_parser.add_argument(
        '-a',
        '--all-branches',
        action='store_true',
        default=False,
        help='Push cache for all branches.')
    push_parser.add_argument(
        '-T',
        '--all-tags',
        action='store_true',
        default=False,
        help='Push cache for all tags.')
    push_parser.add_argument(
        '-d',
        '--with-deps',
        action='store_true',
        default=False,
        help='Push cache for all dependencies of the specified target.')
    push_parser.add_argument(
        '-R',
        '--recursive',
        action='store_true',
        default=False,
        help='Push cache from subdirectories of specified directory.')
    push_parser.set_defaults(func=CmdDataPush)

    # Fetch
    FETCH_HELP = 'Fetch data files from the cloud.'
    fetch_parser = subparsers.add_parser(
        'fetch',
        parents=[parent_cache_parser],
        description=FETCH_HELP,
        help=FETCH_HELP)
    fetch_parser.add_argument(
        '-r',
        '--remote',
        help='Remote repository to fetch from.')
    fetch_parser.add_argument(
        '-a',
        '--all-branches',
        action='store_true',
        default=False,
        help='Fetch cache for all branches.')
    fetch_parser.add_argument(
        '-T',
        '--all-tags',
        action='store_true',
        default=False,
        help='Fetch cache for all tags.')
    fetch_parser.add_argument(
        '-d',
        '--with-deps',
        action='store_true',
        default=False,
        help='Fetch cache for all dependencies of the '
        'specified target.')
    fetch_parser.add_argument(
        '-R',
        '--recursive',
        action='store_true',
        default=False,
        help='Fetch cache for subdirectories of specified directory.')
    fetch_parser.set_defaults(func=CmdDataFetch)

    # Status
    STATUS_HELP = 'Show the project status.'
    status_parser = subparsers.add_parser(
        'status',
        parents=[parent_cache_parser],
        description=STATUS_HELP,
        help=STATUS_HELP)
    status_parser.add_argument(
        '-c',
        '--cloud',
        action='store_true',
        default=False,
        help='Show status of a local cache compared to a remote repository.')
    status_parser.add_argument(
        '-r',
        '--remote',
        help='Remote repository to compare local cache to.')
    status_parser.add_argument(
        '-a',
        '--all-branches',
        action='store_true',
        default=False,
        help='Show status of a local cache compared to a remote repository '
             'for all branches.')
    status_parser.add_argument(
        '-T',
        '--all-tags',
        action='store_true',
        default=False,
        help='Show status of a local cache compared to a remote repository '
             'for all tags.')
    status_parser.add_argument(
        '-d',
        '--with-deps',
        action='store_true',
        default=False,
        help='Show status for all dependencies of the specified target.')
    status_parser.set_defaults(func=CmdDataStatus)
