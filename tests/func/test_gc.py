import os

import configobj
import pytest
from git import Repo

from dvc.exceptions import CollectCacheError
from dvc.main import main
from dvc.repo import Repo as DvcRepo
from dvc.remote.local import RemoteLOCAL
from tests.basic_env import TestDir, TestDvcGit


class TestGC(TestDvcGit):
    def setUp(self):
        super().setUp()

        self.dvc.add(self.FOO)
        self.dvc.add(self.DATA_DIR)
        self.good_cache = [
            self.dvc.cache.local.get(md5) for md5 in self.dvc.cache.local.all()
        ]

        self.bad_cache = []
        for i in ["123", "234", "345"]:
            path = os.path.join(self.dvc.cache.local.cache_dir, i[0:2], i[2:])
            self.create(path, i)
            self.bad_cache.append(path)

    def test_api(self):
        self.dvc.gc()
        self._test_gc()

    def test_cli(self):
        ret = main(["gc", "-f"])
        self.assertEqual(ret, 0)
        self._test_gc()

    def _test_gc(self):
        self.assertTrue(os.path.isdir(self.dvc.cache.local.cache_dir))
        for c in self.bad_cache:
            self.assertFalse(os.path.exists(c))

        for c in self.good_cache:
            self.assertTrue(os.path.exists(c))


class TestGCBranchesTags(TestDvcGit):
    def _check_cache(self, num):
        total = 0
        for root, dirs, files in os.walk(os.path.join(".dvc", "cache")):
            total += len(files)
        self.assertEqual(total, num)

    def test(self):
        fname = "file"

        with open(fname, "w+") as fobj:
            fobj.write("v1.0")

        stages = self.dvc.add(fname)
        self.assertEqual(len(stages), 1)
        self.dvc.scm.add([".gitignore", stages[0].relpath])
        self.dvc.scm.commit("v1.0")
        self.dvc.scm.tag("v1.0")

        self.dvc.scm.checkout("test", create_new=True)
        self.dvc.remove(stages[0].relpath, outs_only=True)
        with open(fname, "w+") as fobj:
            fobj.write("test")
        stages = self.dvc.add(fname)
        self.assertEqual(len(stages), 1)
        self.dvc.scm.add([".gitignore", stages[0].relpath])
        self.dvc.scm.commit("test")

        self.dvc.scm.checkout("master")
        self.dvc.remove(stages[0].relpath, outs_only=True)
        with open(fname, "w+") as fobj:
            fobj.write("trash")
        stages = self.dvc.add(fname)
        self.assertEqual(len(stages), 1)
        self.dvc.scm.add([".gitignore", stages[0].relpath])
        self.dvc.scm.commit("trash")

        self.dvc.remove(stages[0].relpath, outs_only=True)
        with open(fname, "w+") as fobj:
            fobj.write("master")
        stages = self.dvc.add(fname)
        self.assertEqual(len(stages), 1)
        self.dvc.scm.add([".gitignore", stages[0].relpath])
        self.dvc.scm.commit("master")

        self._check_cache(4)

        self.dvc.gc(all_tags=True, all_branches=True)

        self._check_cache(3)

        self.dvc.gc(all_tags=False, all_branches=True)

        self._check_cache(2)

        self.dvc.gc(all_tags=True, all_branches=False)

        self._check_cache(1)


class TestGCMultipleDvcRepos(TestDvcGit):
    def _check_cache(self, num):
        total = 0
        for root, dirs, files in os.walk(os.path.join(".dvc", "cache")):
            total += len(files)
        self.assertEqual(total, num)

    def setUp(self):
        super().setUp()
        self.additional_path = TestDir.mkdtemp()
        self.additional_git = Repo.init(self.additional_path)
        self.additional_dvc = DvcRepo.init(self.additional_path)

        cache_path = os.path.join(self._root_dir, ".dvc", "cache")
        config_path = os.path.join(
            self.additional_path, ".dvc", "config.local"
        )
        cfg = configobj.ConfigObj()
        cfg.filename = config_path
        cfg["cache"] = {"dir": cache_path}
        cfg.write()

        self.additional_dvc = DvcRepo(self.additional_path)

    def test(self):

        # ADD FILE ONLY IN MAIN PROJECT
        fname = "only_in_first"
        with open(fname, "w+") as fobj:
            fobj.write("only in main repo")

        stages = self.dvc.add(fname)
        self.assertEqual(len(stages), 1)

        # ADD FILE IN MAIN PROJECT THAT IS ALSO IN SECOND PROJECT
        fname = "in_both"
        with open(fname, "w+") as fobj:
            fobj.write("in both repos")

        stages = self.dvc.add(fname)
        self.assertEqual(len(stages), 1)

        cwd = os.getcwd()
        os.chdir(self.additional_path)
        # ADD FILE ONLY IN SECOND PROJECT
        fname = os.path.join(self.additional_path, "only_in_second")
        with open(fname, "w+") as fobj:
            fobj.write("only in additional repo")

        stages = self.additional_dvc.add(fname)
        self.assertEqual(len(stages), 1)

        # ADD FILE IN SECOND PROJECT THAT IS ALSO IN MAIN PROJECT
        fname = os.path.join(self.additional_path, "in_both")
        with open(fname, "w+") as fobj:
            fobj.write("in both repos")

        stages = self.additional_dvc.add(fname)
        self.assertEqual(len(stages), 1)

        os.chdir(cwd)

        self._check_cache(3)

        self.dvc.gc(repos=[self.additional_path])
        self._check_cache(3)

        self.dvc.gc()
        self._check_cache(2)


def test_all_commits(tmp_dir, scm, dvc):
    tmp_dir.dvc_gen("testfile", "uncommitted")
    tmp_dir.dvc_gen("testfile", "committed", commit="committed")
    tmp_dir.dvc_gen("testfile", "modified", commit="modified")
    tmp_dir.dvc_gen("testfile", "workspace")

    n = _count_files(dvc.cache.local.cache_dir)
    dvc.gc(all_commits=True)

    # Only one uncommitted file should go away
    assert _count_files(dvc.cache.local.cache_dir) == n - 1


def test_gc_no_dir_cache(tmp_dir, dvc, repo_template):
    dvc.add(["foo", "bar"])
    dir_stage, = dvc.add("dir")

    os.unlink(dir_stage.outs[0].cache_path)

    with pytest.raises(CollectCacheError):
        dvc.gc()

    assert _count_files(dvc.cache.local.cache_dir) == 4
    dvc.gc(force=True)
    assert _count_files(dvc.cache.local.cache_dir) == 2


def _count_files(path):
    return sum(len(files) for _, _, files in os.walk(path))


def test_gc_no_unpacked_dir(tmp_dir, dvc, repo_template):
    dir_stages = dvc.add("dir")
    dvc.status()

    os.remove("dir.dvc")
    unpackeddir = (
        dir_stages[0].outs[0].cache_path + RemoteLOCAL.UNPACKED_DIR_SUFFIX
    )

    assert os.path.exists(unpackeddir)

    dvc.gc(force=True)
    assert not os.path.exists(unpackeddir)
