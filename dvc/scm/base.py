"""Manages source control systems(e.g. Git) in dvc."""

from __future__ import unicode_literals

import os

from dvc.exceptions import DvcException


class SCMError(DvcException):
    """Base class for source control management errors."""


class FileNotInRepoError(DvcException):
    """Thrown when trying to find .gitignore for a file that is not in a scm
    repository.
    """


class Base(object):
    """Base class for source control management driver implementations."""

    def __init__(self, root_dir=os.curdir, repo=None):
        self.repo = repo
        self.root_dir = root_dir

    def __repr__(self):
        return "{class_name}: '{directory}'".format(
            class_name=type(self).__name__, directory=self.dir
        )

    @property
    def dir(self):
        """Path to a directory with SCM specific information."""
        return None

    @staticmethod
    def is_repo(root_dir):  # pylint: disable=unused-argument
        """Returns whether or not root_dir is a valid SCM repository."""
        return True

    @staticmethod
    def is_submodule(root_dir):  # pylint: disable=unused-argument
        """Returns whether or not root_dir is a valid SCM repository
        submodule.
        """
        return True

    def ignore(self, path):  # pylint: disable=unused-argument
        """Makes SCM ignore a specified path."""

    def ignore_remove(self, path):  # pylint: disable=unused-argument
        """Makes SCM stop ignoring a specified path."""

    @property
    def ignore_file(self):
        """Filename for a file that contains ignored paths for this SCM."""

    def ignore_list(self, p_list):
        """Makes SCM ignore all paths specified in a list."""
        return [self.ignore(path) for path in p_list]

    def add(self, paths):
        """Makes SCM start tracking every path from a specified list of paths.
        """

    def commit(self, msg):
        """Makes SCM create a commit."""

    def checkout(self, branch, create_new=False):
        """Makes SCM checkout a branch."""

    def branch(self, branch):
        """Makes SCM create a branch with a specified name."""

    def tag(self, tag):
        """Makes SCM create a tag with a specified name."""

    def brancher(
        self, branches=None, all_branches=False, tags=None, all_tags=False
    ):
        """Generator that iterates over specified revisions.

        Args:
            branches (list): a list of branches to iterate over.
            all_branches (bool): iterate over all available branches.
            tags (list): a list of tags to iterate over.
            all_tags (bool): iterate over all available tags.

        Yields:
            str: the current revision.
        """
        if not branches and not all_branches and not tags and not all_tags:
            yield ""
            return

        saved = self.active_branch()
        revs = []

        if all_branches:
            branches = self.list_branches()

        if all_tags:
            tags = self.list_tags()

        if branches is None:
            revs.extend([saved])
        else:
            revs.extend(branches)

        if tags is not None:
            revs.extend(tags)

        for rev in revs:
            self.checkout(rev)
            yield rev

        self.checkout(saved)

    def untracked_files(self):  # pylint: disable=no-self-use
        """Returns a list of untracked files."""
        return []

    def is_tracked(self, path):  # pylint: disable=no-self-use, unused-argument
        """Returns whether or not a specified path is tracked."""
        return False

    def active_branch(self):  # pylint: disable=no-self-use
        """Returns current branch in the repo."""
        return ""

    def list_branches(self):  # pylint: disable=no-self-use
        """Returns a list of available branches in the repo."""
        return []

    def list_tags(self):  # pylint: disable=no-self-use
        """Returns a list of available tags in the repo."""
        return []

    def install(self):
        """Adds dvc commands to SCM hooks for the repo."""
