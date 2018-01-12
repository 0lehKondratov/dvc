import os
import stat
import shutil
import filecmp

from dvc.main import main
from dvc.command.repro import CmdRepro
from dvc.project import ReproductionError
from dvc.data_cloud import file_md5

from tests.basic_env import TestDvc


class TestRepro(TestDvc):
    def setUp(self):
        super(TestRepro, self).setUp()

        self.foo_stage = self.dvc.add(self.FOO)

        self.file1 = 'file1'
        self.file1_stage = self.file1 + '.dvc'
        self.dvc.run(fname=self.file1_stage,
                     outs=[self.file1],
                     deps=[self.FOO, self.CODE],
                     cmd='python {} {} {}'.format(self.CODE, self.FOO, self.file1))


class TestReproChangedCode(TestRepro):
    def test(self):
        repro = 'repro'
        with open(self.CODE, 'a') as code:
            code.write("\nshutil.copyfile('{}', sys.argv[2])\n".format(self.BAR))

        self.dvc.reproduce(self.file1_stage)

        self.assertTrue(filecmp.cmp(self.file1, self.BAR))


class TestReproChangedData(TestRepro):
    def test(self):
        self.swap_foo_with_bar()

        self.dvc.reproduce(self.file1_stage)

        self.assertTrue(filecmp.cmp(self.file1, self.BAR))

    def swap_foo_with_bar(self):
        os.chmod(self.FOO, stat.S_IWRITE)
        os.unlink(self.FOO)
        shutil.copyfile(self.BAR, self.FOO)


class TestReproChangedDeepData(TestReproChangedData):
    def test(self):
        file2 = 'file2'
        file2_stage = file2 + '.dvc'
        self.dvc.run(fname=file2_stage,
                     outs=[file2],
                     deps=[self.file1, self.CODE],
                     cmd='python {} {} {}'.format(self.CODE, self.file1, file2))

        self.swap_foo_with_bar()

        self.dvc.reproduce(file2_stage)

        self.assertTrue(filecmp.cmp(self.file1, self.BAR))
        self.assertTrue(filecmp.cmp(file2, self.BAR))


class TestReproPhony(TestReproChangedData):
    def test(self):
        stage = self.dvc.run(deps=[self.file1])

        self.swap_foo_with_bar()

        self.dvc.reproduce(stage.path)

        self.assertTrue(filecmp.cmp(self.file1, self.BAR))


class TestNonExistingOutput(TestRepro):
    def test(self):
        os.chmod(self.FOO, stat.S_IWRITE)
        os.unlink(self.FOO)

        with self.assertRaises(ReproductionError) as cx:
            self.dvc.reproduce(self.file1_stage)


class TestReproDataSource(TestReproChangedData):
    def test(self):
        self.swap_foo_with_bar()

        stages = self.dvc.reproduce(self.foo_stage.path)

        self.assertTrue(filecmp.cmp(self.FOO, self.BAR))
        self.assertEqual(stages[0].outs[0].md5, file_md5(self.BAR)[0])


class TestCmdRepro(TestRepro):
    def test(self):
        ret = main(['repro',
                    self.file1_stage])
        self.assertEqual(ret, 0)

        ret = main(['repro',
                    'non-existing-file'])
        self.assertNotEqual(ret, 0)
