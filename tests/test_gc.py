import os
import shutil
import filecmp

from dvc.main import main
from dvc.data_cloud import file_md5
from dvc.stage import Stage, CmdOutputNoCacheError, CmdOutputOutsideOfRepoError
from dvc.stage import CmdOutputDoesNotExistError, CmdOutputIsNotFileError
from dvc.project import StageNotFoundError
from dvc.command.gc import CmdGC

from tests.basic_env import TestDvc


class TestGC(TestDvc):
    def setUp(self):
        super(TestGC, self).setUp()

        stage = self.dvc.add(self.FOO)
        self.good_cache = self.dvc.cache.all()

        self.bad_cache = []
        for i in ['1', '2', '3']:
            path = os.path.join(self.dvc.cache.cache_dir, i)
            self.create(path, i)
            self.bad_cache.append(path)

    def test_api(self):
        self.dvc.gc()
        self._test_gc()

    def test_cli(self):
        ret = main(['gc'])
        self._test_gc()

    def _test_gc(self):
        self.assertTrue(os.path.isdir(self.dvc.cache.cache_dir))
        for c in self.bad_cache:
            self.assertFalse(os.path.exists(c))

        for c in self.good_cache:
            self.assertTrue(os.path.exists(c))
            self.assertTrue(os.path.isfile(c))
