import logging
import os
import re
from itertools import groupby

from pathspec.patterns import GitWildMatchPattern
from pathspec.util import normalize_file
from pygtrie import StringTrie

from dvc.path_info import PathInfo
from dvc.pathspec_math import merge_patterns
from dvc.system import System
from dvc.utils import relpath

logger = logging.getLogger(__name__)


class DvcIgnore:
    DVCIGNORE_FILE = ".dvcignore"

    def __call__(self, root, dirs, files):
        raise NotImplementedError


class DvcIgnorePatterns(DvcIgnore):
    def __init__(self, pattern_list, dirname):

        self.pattern_list = pattern_list
        self.dirname = dirname
        self.prefix = self.dirname + os.sep

        regex_pattern_list = map(
            GitWildMatchPattern.pattern_to_regex, pattern_list
        )

        self.ignore_spec = [
            (ignore, re.compile("|".join(item[0] for item in group)))
            for ignore, group in groupby(regex_pattern_list, lambda x: x[1])
            if ignore is not None
        ]

    @classmethod
    def from_files(cls, ignore_file_path, tree):
        assert os.path.isabs(ignore_file_path)
        dirname = os.path.normpath(os.path.dirname(ignore_file_path))
        with tree.open(ignore_file_path, encoding="utf-8") as fobj:
            path_spec_lines = [
                line for line in map(str.strip, fobj.readlines()) if line
            ]

        return cls(path_spec_lines, dirname)

    def __call__(self, root, dirs, files):
        files = [f for f in files if not self.matches(root, f)]
        dirs = [d for d in dirs if not self.matches(root, d, True)]

        return dirs, files

    def matches(self, dirname, basename, is_dir=False):
        # NOTE: `relpath` is too slow, so we have to assume that both
        # `dirname` and `self.dirname` are relative or absolute together.
        if dirname == self.dirname:
            path = basename
        elif dirname.startswith(self.prefix):
            rel = dirname[len(self.prefix) :]
            # NOTE: `os.path.join` is ~x5.5 slower
            path = f"{rel}{os.sep}{basename}"
        else:
            return False

        if not System.is_unix():
            path = normalize_file(path)
        return self.ignore(path, is_dir)

    def ignore(self, path, is_dir):
        result = False
        if is_dir:
            path_dir = f"{path}/"
            for ignore, pattern in self.ignore_spec:
                if pattern.match(path) or pattern.match(path_dir):
                    result = ignore
        else:
            for ignore, pattern in self.ignore_spec:
                if pattern.match(path):
                    result = ignore
        return result

    def __hash__(self):
        return hash(self.dirname + ":" + "\n".join(self.pattern_list))

    def __eq__(self, other):
        if not isinstance(other, DvcIgnorePatterns):
            return NotImplemented
        return (self.dirname == other.dirname) & (
            self.pattern_list == other.pattern_list
        )

    def __bool__(self):
        return bool(self.pattern_list)


class DvcIgnoreFilterNoop:
    def __init__(self, tree, root_dir):
        pass

    def __call__(self, root, dirs, files):
        return dirs, files

    def is_ignored_dir(self, _):
        return False

    def is_ignored_file(self, _):
        return False


class DvcIgnoreFilter:
    @staticmethod
    def _is_dvc_repo(root, directory):
        from dvc.repo import Repo

        return os.path.isdir(os.path.join(root, directory, Repo.DVC_DIR))

    def __init__(self, tree, root_dir):
        from dvc.repo import Repo

        default_ignore_patterns = [".hg/", ".git/", "{}/".format(Repo.DVC_DIR)]

        self.tree = tree
        self.root_dir = root_dir
        self.ignores_trie_tree = StringTrie(separator=os.sep)
        self.ignores_trie_tree[root_dir] = DvcIgnorePatterns(
            default_ignore_patterns, root_dir
        )
        for root, dirs, _ in self.tree.walk(
            self.root_dir, use_dvcignore=False
        ):
            self._update(root)
            self._update_sub_repo(root, dirs)
            dirs[:], _ = self(root, dirs, [])

    def _update(self, dirname):
        ignore_file_path = os.path.join(dirname, DvcIgnore.DVCIGNORE_FILE)
        if self.tree.exists(ignore_file_path, use_dvcignore=False):
            new_pattern = DvcIgnorePatterns.from_files(
                ignore_file_path, self.tree
            )
            old_pattern = self._get_trie_pattern(dirname)
            if old_pattern:
                self.ignores_trie_tree[dirname] = DvcIgnorePatterns(
                    *merge_patterns(
                        old_pattern.pattern_list,
                        old_pattern.dirname,
                        new_pattern.pattern_list,
                        new_pattern.dirname,
                    )
                )
            else:
                self.ignores_trie_tree[dirname] = new_pattern

    def _update_sub_repo(self, root, dirs):
        for d in dirs:
            if self._is_dvc_repo(root, d):
                old_pattern = self._get_trie_pattern(root)
                if old_pattern:
                    self.ignores_trie_tree[root] = DvcIgnorePatterns(
                        *merge_patterns(
                            old_pattern.pattern_list,
                            old_pattern.dirname,
                            ["/{}/".format(d)],
                            root,
                        )
                    )
                else:
                    self.ignores_trie_tree[root] = DvcIgnorePatterns(
                        ["/{}/".format(d)], root
                    )

    def __call__(self, root, dirs, files):
        ignore_pattern = self._get_trie_pattern(root)
        if ignore_pattern:
            return ignore_pattern(root, dirs, files)
        else:
            return dirs, files

    def _get_trie_pattern(self, dirname):
        ignore_pattern = self.ignores_trie_tree.longest_prefix(dirname).value
        return ignore_pattern

    def _is_ignored(self, path, is_dir=False):
        if self._outside_repo(path):
            return True
        dirname, basename = os.path.split(os.path.normpath(path))
        ignore_pattern = self._get_trie_pattern(dirname)
        if ignore_pattern:
            return ignore_pattern.matches(dirname, basename, is_dir)
        else:
            return False

    def is_ignored_dir(self, path):
        path = os.path.abspath(path)
        if path == self.root_dir:
            return False

        return self._is_ignored(path, True)

    def is_ignored_file(self, path):
        return self._is_ignored(path, False)

    def _outside_repo(self, path):
        path = PathInfo(path)

        # paths outside of the repo should be ignored
        path = relpath(path, self.root_dir)
        if path.startswith("..") or (
            os.name == "nt"
            and not os.path.commonprefix(
                [os.path.abspath(path), self.root_dir]
            )
        ):
            return True
        return False
