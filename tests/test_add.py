import os
import shutil
import filecmp

from dvc.main import main
from dvc.data_cloud import file_md5
from dvc.stage import Stage, CmdOutputNoCacheError, CmdOutputOutsideOfRepoError
from dvc.stage import CmdOutputDoesNotExistError, CmdOutputIsNotFileError
from dvc.stage import CmdOutputAlreadyTrackedError
from dvc.project import StageNotFoundError
from dvc.command.add import CmdAdd

from tests.basic_env import TestDvc


class TestAdd(TestDvc):
    def test(self):
        md5 = file_md5(self.FOO)[0]

        stage = self.dvc.add(self.FOO)

        self.assertIsInstance(stage, Stage)
        self.assertTrue(os.path.isfile(stage.path))
        self.assertEqual(len(stage.outs), 1)
        self.assertEqual(len(stage.deps), 0)
        self.assertEqual(stage.cmd, None)
        self.assertEqual(stage.outs[0].md5, md5)


class TestAddNonExistentFile(TestDvc):
    def test(self):
        with self.assertRaises(CmdOutputDoesNotExistError) as cx:
            self.dvc.add('non_existent_file')


class TestAddFileOutsideOfRepo(TestDvc):
    def test(self):
        with self.assertRaises(CmdOutputOutsideOfRepoError) as cx:
            self.dvc.add(os.path.join(os.path.dirname(self.dvc.root_dir), self.FOO))


class TestAddDirectory(TestDvc):
    def test(self):
        dname = 'directory'
        os.mkdir(dname)
        with self.assertRaises(CmdOutputIsNotFileError) as cx:
            self.dvc.add(dname)


class TestAddTrackedFile(TestDvc):
    def test(self):
        fname = 'tracked_file'
        self.create(fname, 'tracked file contents')
        self.dvc.scm.add([fname])
        self.dvc.scm.commit('add {}'.format(fname))

        with self.assertRaises(CmdOutputAlreadyTrackedError) as cx:
            self.dvc.add(fname)


class TestCmdAdd(TestDvc):
    def test(self):
        ret = main(['add',
                    self.FOO])
        self.assertEqual(ret, 0)

        ret = main(['add',
                    'non-existing-file'])
        self.assertNotEqual(ret, 0)
