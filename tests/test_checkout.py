import os
import yaml
import time
import stat
import shutil
import filecmp

from dvc.main import main
from tests.basic_env import TestDvc
from tests.test_repro import TestRepro
from dvc.stage import Stage
from dvc.remote.local import RemoteLOCAL


class TestCheckout(TestRepro):
    def setUp(self):
        super(TestCheckout, self).setUp()


        stages = self.dvc.add(self.DATA_DIR)
        self.assertEqual(len(stages), 1)
        self.data_dir_stage = stages[0]
        self.assertTrue(self.data_dir_stage is not None)

        self.orig = 'orig'
        shutil.copy(self.FOO, self.orig)
        os.unlink(self.FOO)

        self.orig_dir = 'orig_dir'
        shutil.copytree(self.DATA_DIR, self.orig_dir)
        shutil.rmtree(self.DATA_DIR)

    def test(self):
        self.dvc.checkout()
        self._test_checkout()

    def _test_checkout(self):
        self.assertTrue(os.path.isfile(self.FOO))
        self.assertTrue(filecmp.cmp(self.FOO, self.orig, shallow=False))


class TestCheckoutSingleStage(TestCheckout):
    def test(self):
        ret = main(['checkout', self.foo_stage.path])
        self.assertEqual(ret, 0)

        ret = main(['checkout', self.data_dir_stage.path])
        self.assertEqual(ret, 0)

        self._test_checkout()


class TestCheckoutCorruptedCacheFile(TestRepro):
    def test(self):
        cache = self.foo_stage.outs[0].cache

        time.sleep(1)

        with open(cache, 'a') as fd:
            fd.write('1')

        self.dvc.checkout()

        self.assertFalse(os.path.isfile(self.FOO))
        self.assertFalse(os.path.isfile(cache))


class TestCmdCheckout(TestCheckout):
    def test(self):
        ret = main(['checkout'])
        self.assertEqual(ret, 0)
        self._test_checkout()


class CheckoutBase(TestDvc):
    GIT_IGNORE = '.gitignore'

    def commit_data_file(self, fname, content='random text'):
        with open(fname, 'w') as fd:
            fd.write(content)
        stages = self.dvc.add(fname)
        self.assertEqual(len(stages), 1)
        self.assertTrue(stages[0] is not None)
        self.dvc.scm.add([fname + '.dvc', '.gitignore'])
        self.dvc.scm.commit('adding ' + fname)

    def read_ignored(self):
        return list(map(lambda s: s.strip('\n'), open(self.GIT_IGNORE).readlines()))


class TestRemoveFilesWhenCheckout(CheckoutBase):
    def test(self):
        fname = 'file_in_a_branch'
        branch_master = 'master'
        branch_1 = 'b1'

        self.dvc.scm.add(self.dvc.scm.untracked_files())
        self.dvc.scm.commit('add all files')

        # add the file into a separate branch
        self.dvc.scm.checkout(branch_1, True)
        ret = main(['checkout'])
        self.assertEqual(ret, 0)
        self.commit_data_file(fname)

        # Checkout back in master
        self.dvc.scm.checkout(branch_master)
        self.assertTrue(os.path.exists(fname))

        # Make sure `dvc checkout` removes the file
        # self.dvc.checkout()
        ret = main(['checkout'])
        self.assertEqual(ret, 0)
        self.assertFalse(os.path.exists(fname))


class TestGitIgnoreBasic(CheckoutBase):
    def test(self):
        fname1 = 'file_1'
        fname2 = 'file_2'
        fname3 = 'file_3'

        self.dvc.scm.add(self.dvc.scm.untracked_files())
        self.dvc.scm.commit('add all files')

        self.assertFalse(os.path.exists(self.GIT_IGNORE))

        self.commit_data_file(fname1)
        self.commit_data_file(fname2)
        self.dvc.run(cmd='python {} {} {}'.format(self.CODE, self.FOO, fname3),
                     deps=[self.CODE, self.FOO],
                     outs_no_cache=[fname3])

        self.assertTrue(os.path.exists(self.GIT_IGNORE))

        ignored = self.read_ignored()

        self.assertEqual(len(ignored), 2)

        self.assertIn(fname1, ignored)
        self.assertIn(fname2, ignored)


class TestGitIgnoreWhenCheckout(CheckoutBase):
    def test(self):
        fname_master = 'file_in_a_master'
        branch_master = 'master'
        fname_branch = 'file_in_a_branch'
        branch_1 = 'b1'

        self.dvc.scm.add(self.dvc.scm.untracked_files())
        self.dvc.scm.commit('add all files')
        self.commit_data_file(fname_master)

        self.dvc.scm.checkout(branch_1, True)
        ret = main(['checkout'])
        self.assertEqual(ret, 0)
        self.commit_data_file(fname_branch)

        self.dvc.scm.checkout(branch_master)
        ret = main(['checkout'])
        self.assertEqual(ret, 0)

        ignored = self.read_ignored()

        self.assertEqual(len(ignored), 1)
        self.assertIn(fname_master, ignored)

        self.dvc.scm.checkout(branch_1)
        ret = main(['checkout'])
        self.assertEqual(ret, 0)
        ignored = self.read_ignored()
        self.assertIn(fname_branch, ignored)


class TestCheckoutMissingMd5InStageFile(TestRepro):
    def test(self):
        with open(self.file1_stage, 'r') as fd:
            d = yaml.load(fd)

        del(d[Stage.PARAM_OUTS][0][RemoteLOCAL.PARAM_MD5])
        del(d[Stage.PARAM_DEPS][0][RemoteLOCAL.PARAM_MD5])

        with open(self.file1_stage, 'w') as fd:
            yaml.dump(d, fd)

        self.dvc.checkout()


class TestCheckoutEmptyDir(TestDvc):
    def test(self):
        dname = 'empty_dir'
        os.mkdir(dname)

        stages = self.dvc.add(dname)
        self.assertEqual(len(stages), 1)
        stage = stages[0]
        self.assertTrue(stage is not None)
        self.assertEqual(len(stage.outs), 1)

        stage.outs[0].remove()
        self.assertFalse(os.path.exists(dname))

        self.dvc.checkout()

        self.assertTrue(os.path.isdir(dname))
        self.assertEqual(len(os.listdir(dname)), 0)


class TestCheckoutNotCachedFile(TestDvc):
    def test(self):
        cmd = 'python {} {} {}'.format(self.CODE, self.FOO, 'out')

        self.dvc.add(self.FOO)
        stage = self.dvc.run(cmd=cmd,
                             deps=[self.FOO, self.CODE],
                             outs_no_cache=['out'])

        self.dvc.checkout()
