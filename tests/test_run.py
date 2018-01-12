import os
import filecmp

from dvc.main import main
from dvc.data_cloud import file_md5
from dvc.stage import Stage
from dvc.command.run import CmdRun

from tests.basic_env import TestDvc


class TestRun(TestDvc):
    def test(self):
        cmd = 'python {} {} {}'.format(self.CODE, self.FOO, 'out')
        deps = [self.FOO, self.CODE]
        outs = [os.path.join(self.dvc.root_dir, 'out')]
        outs_no_cache = []
        fname = os.path.join(self.dvc.root_dir, 'out.dvc')
        cwd = os.curdir

        self.dvc.add(self.FOO)
        stage = self.dvc.run(cmd=cmd,
                             deps=deps,
                             outs=outs,
                             outs_no_cache=outs_no_cache,
                             fname=fname,
                             cwd=cwd)

        self.assertTrue(filecmp.cmp(self.FOO, 'out'))
        self.assertTrue(os.path.isfile(stage.path))
        self.assertEqual(stage.cmd, cmd)
        self.assertEqual(len(stage.deps), len(deps))
        self.assertEqual(len(stage.outs), len(outs + outs_no_cache))
        self.assertEqual(stage.outs[0].path, outs[0])
        self.assertEqual(stage.outs[0].md5, file_md5(self.FOO)[0])  
        self.assertTrue(stage.path, fname)


class TestRunEmpty(TestDvc):
    def test(self):
        self.dvc.run(cmd='',
                     deps=[],
                     outs=[],
                     outs_no_cache=[],
                     fname='empty.dvc',
                     cwd=os.curdir)


class TestRunNoExec(TestDvc):
    def test(self):
        self.dvc.run(cmd='python {} {} {}'.format(self.CODE, self.FOO, 'out'),
                     no_exec=True)
        self.assertFalse(os.path.exists('out'))


class TestCmdRun(TestDvc):
    def test_run(self):
        ret = main(['run',
                    '-d', self.FOO,
                    '-d', self.CODE,
                    '-o', 'out',
                    '-f', 'out.dvc',
                    'python', self.CODE, self.FOO, 'out'])
        self.assertEqual(ret, 0)
        self.assertTrue(os.path.isfile('out'))
        self.assertTrue(os.path.isfile('out.dvc'))
        self.assertTrue(filecmp.cmp(self.FOO, 'out'))

    def test_run_bad_command(self):
        ret = main(['run',
                    'non-existing-command'])
        self.assertNotEqual(ret, 0)

    def test_stage_file_name(self):
        fname = 'path/to/file'
        outs = [fname, 'dummy']
        dvcfile = os.path.basename(fname) + Stage.STAGE_FILE_SUFFIX

        ret = CmdRun.stage_file_name(None, [], [])
        self.assertEqual(ret, Stage.STAGE_FILE)

        ret = CmdRun.stage_file_name(None, [], outs)
        self.assertEqual(ret, dvcfile)

        ret = CmdRun.stage_file_name(None, outs, [])
        self.assertEqual(ret, dvcfile)

        ret = CmdRun.stage_file_name(fname, [], [])
        self.assertEqual(ret, fname)
