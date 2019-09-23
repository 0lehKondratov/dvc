"""Manages dvc remotes that user can use with push/pull/status comamnds."""

from __future__ import unicode_literals

import logging

from dvc.config import Config, NoRemoteError
from dvc.remote import Remote


logger = logging.getLogger(__name__)


class DataCloud(object):
    """Class that manages dvc remotes.

    Args:
        repo (dvc.repo.Repo): repo instance that belongs to the repo that
            we are working on.

    Raises:
        config.ConfigError: thrown when config has invalid format.
    """

    def __init__(self, repo):
        self.repo = repo

    @property
    def _config(self):
        return self.repo.config.config

    @property
    def _core(self):
        return self._config.get(Config.SECTION_CORE, {})

    def get_remote(self, remote=None, command="<command>"):
        if not remote:
            remote = self._core.get(Config.SECTION_CORE_REMOTE)

        if remote:
            return self._init_remote(remote)

        raise NoRemoteError(command)

    def _init_remote(self, remote):
        return Remote(self.repo, name=remote)

    def push(self, targets, jobs=None, remote=None, show_checksums=False):
        """Push data items in a cloud-agnostic way.

        Args:
            targets (list): list of targets to push to the cloud.
            jobs (int): number of jobs that can be running simultaneously.
            remote (dvc.remote.base.RemoteBASE): optional remote to push to.
                By default remote from core.remote config option is used.
            show_checksums (bool): show checksums instead of file names in
                information messages.
        """
        return self.repo.cache.local.push(
            targets,
            jobs=jobs,
            remote=self.get_remote(remote, "push"),
            show_checksums=show_checksums,
        )

    def pull(self, targets, jobs=None, remote=None, show_checksums=False):
        """Pull data items in a cloud-agnostic way.

        Args:
            targets (list): list of targets to pull from the cloud.
            jobs (int): number of jobs that can be running simultaneously.
            remote (dvc.remote.base.RemoteBASE): optional remote to pull from.
                By default remote from core.remote config option is used.
            show_checksums (bool): show checksums instead of file names in
                information messages.
        """
        return self.repo.cache.local.pull(
            targets,
            jobs=jobs,
            remote=self.get_remote(remote, "pull"),
            show_checksums=show_checksums,
        )

    def status(self, targets, jobs=None, remote=None, show_checksums=False):
        """Check status of data items in a cloud-agnostic way.

        Args:
            targets (list): list of targets to check status for.
            jobs (int): number of jobs that can be running simultaneously.
            remote (dvc.remote.base.RemoteBASE): optional remote to compare
                targets to. By default remote from core.remote config option
                is used.
            show_checksums (bool): show checksums instead of file names in
                information messages.
        """
        remote = self.get_remote(remote, "status")
        return self.repo.cache.local.status(
            targets, jobs=jobs, remote=remote, show_checksums=show_checksums
        )
