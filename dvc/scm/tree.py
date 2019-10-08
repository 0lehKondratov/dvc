import os

from dvc.utils import dvc_walk
from dvc.utils.compat import open


class BaseTree(object):
    """Abstract class to represent access to files"""

    @property
    def tree_root(self):
        pass

    def open(self, path, mode="r", encoding="utf-8"):
        """Open file and return a stream."""

    def exists(self, path):
        """Test whether a path exists."""

    def isdir(self, path):
        """Return true if the pathname refers to an existing directory."""

    def isfile(self, path):
        """Test whether a path is a regular file"""

    def walk(self, top, topdown=True, dvcignore=None):
        """Directory tree generator.

        See `os.walk` for the docs. Differences:
        - no support for symlinks
        - it could raise exceptions, there is no onerror argument
        """


class WorkingTree(BaseTree):
    """Proxies the repo file access methods to working tree files"""

    def __init__(self, repo_root=os.getcwd()):
        self.repo_root = repo_root

    @property
    def tree_root(self):
        return self.repo_root

    def open(self, path, mode="r", encoding="utf-8"):
        """Open file and return a stream."""
        return open(path, mode=mode, encoding=encoding)

    def exists(self, path):
        """Test whether a path exists."""
        return os.path.exists(path)

    def isdir(self, path):
        """Return true if the pathname refers to an existing directory."""
        return os.path.isdir(path)

    def isfile(self, path):
        """Test whether a path is a regular file"""
        return os.path.isfile(path)

    def walk(self, top, topdown=True, dvcignore=None):
        """Directory tree generator.

        See `os.walk` for the docs. Differences:
        - no support for symlinks
        - it could raise exceptions, there is no onerror argument
        """

        assert dvcignore

        def onerror(e):
            raise e

        for root, dirs, files in dvc_walk(
            os.path.abspath(top), dvcignore, topdown=topdown, onerror=onerror
        ):
            yield os.path.normpath(root), dirs, files
