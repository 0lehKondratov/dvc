import os
from filecmp import dircmp

from dvc.scm import Git
from mock import MagicMock
from contextlib import contextmanager


def spy(method_to_decorate):
    mock = MagicMock()

    def wrapper(self, *args, **kwargs):
        mock(*args, **kwargs)
        return method_to_decorate(self, *args, **kwargs)

    wrapper.mock = mock
    return wrapper


def get_gitignore_content():
    with open(Git.GITIGNORE, "r") as gitignore:
        return gitignore.read().splitlines()


@contextmanager
def cd(newdir):
    prevdir = os.getcwd()
    os.chdir(os.path.expanduser(newdir))
    try:
        yield
    finally:
        os.chdir(prevdir)


def trees_equal(dir_path_1, dir_path_2):

    comparison = dircmp(dir_path_1, dir_path_2)

    assert set(comparison.left_only) == set(comparison.right_only) == set()

    for d in comparison.common_dirs:
        trees_equal(os.path.join(dir_path_1, d), os.path.join(dir_path_2, d))


def to_posixpath(path):
    return path.replace("\\", "/")
