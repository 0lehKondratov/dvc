import os

from dvc.cli import parse_args
from dvc.command.init import CmdInit
from dvc.command.run import CmdRun
from dvc.command.data_sync import CmdDataPull, CmdDataPush, CmdDataStatus
from dvc.command.repro import CmdRepro
from dvc.command.add import CmdAdd
from dvc.command.remove import CmdRemove
from dvc.command.gc import CmdGC
from dvc.command.config import CmdConfig
from dvc.command.show_pipeline import CmdShowPipeline
from dvc.command.show_workflow import CmdShowWorkflow
from dvc.command.checkout import CmdCheckout
from dvc.command.fsck import CmdFsck

from tests.basic_env import TestDvc


class TestArgParse(TestDvc):
    def test(self):
        args = parse_args(['init'])
        self.assertIsInstance(args.func(args), CmdInit)


class TestRun(TestDvc):
    def test(self):
        dep1 = 'dep1'
        dep2 = 'dep2'

        out1 = 'out1'
        out2 = 'out2'

        out_no_cache1 = 'out_no_cache1'
        out_no_cache2 = 'out_no_cache2'

        fname = 'dvc.dvc'
        cwd = os.curdir
        cmd = 'cmd'
        arg1 = 'arg1'
        arg2 = 'arg2'

        args = parse_args(['run',
                           '-d', dep1,
                           '--deps', dep2,
                           '-o', out1,
                           '--outs', out2,
                           '-O', out_no_cache1,
                           '--outs-no-cache', out_no_cache2,
                           '-f', fname,
                           '--file', fname,
                           '-c', cwd,
                           cmd, arg1, arg2])

        self.assertIsInstance(args.func(args), CmdRun)
        self.assertEqual(args.deps, [dep1, dep2])
        self.assertEqual(args.outs, [out1, out2])
        self.assertEqual(args.outs_no_cache, [out_no_cache1, out_no_cache2])
        self.assertEqual(args.file, fname)
        self.assertEqual(args.cwd, cwd)
        self.assertEqual(args.command, [cmd, arg1, arg2])


class TestPull(TestDvc):
    def test(self):
        args = parse_args(['pull'])
        self.assertIsInstance(args.func(args), CmdDataPull)


class TestPush(TestDvc):
    def test(self):
        args = parse_args(['push'])
        self.assertIsInstance(args.func(args), CmdDataPush)


class TestStatus(TestDvc):
    def test(self):
        args = parse_args(['status'])
        self.assertIsInstance(args.func(args), CmdDataStatus)


class TestRepro(TestDvc):
    def test(self):
        target1 = '1'
        target2 = '2'

        args = parse_args(['repro',
                           target1,
                           target2,
                           '-f', '--force',
                           '-s', '--single-item'])

        self.assertIsInstance(args.func(args), CmdRepro)
        self.assertEqual(args.targets, [target1, target2])
        self.assertEqual(args.force, True)
        self.assertEqual(args.single_item, True)


class TestRemove(TestDvc):
    def test(self):
        target1 = '1'
        target2 = '2'

        args = parse_args(['remove',
                           target1,
                           target2])

        self.assertIsInstance(args.func(args), CmdRemove)
        self.assertEqual(args.targets, [target1, target2])


class TestAdd(TestDvc):
    def test(self):
        target1 = '1'
        target2 = '2'

        args = parse_args(['add',
                           target1,
                           target2])

        self.assertIsInstance(args.func(args), CmdAdd)
        self.assertEqual(args.targets, [target1, target2])


class TestGC(TestDvc):
    def test(self):
        args = parse_args(['gc'])
        self.assertIsInstance(args.func(args), CmdGC)


class TestConfig(TestDvc):
    def test(self):
        name = 'param'
        value = '1'

        args = parse_args(['config',
                           '-u', '--unset',
                           name,
                           value])

        self.assertIsInstance(args.func(args), CmdConfig)
        self.assertEqual(args.unset, True)
        self.assertEqual(args.name, name)
        self.assertEqual(args.value, value)


class TestShowPipeline(TestDvc):
    def test(self):
        target1 = '1'
        target2 = '2'

        args = parse_args(['show',
                           'pipeline',
                           target1,
                           target2])

        self.assertIsInstance(args.func(args), CmdShowPipeline)
        self.assertEqual(args.target, [target1, target2])


class TestShowWorkflow(TestDvc):
    def test(self):
        target = '1'

        args = parse_args(['show',
                           'workflow',
                           '-d', '--dvc-commits',
                           '-a', '--all-commits',
                           '-m', '1',
                           '--max-commits', '1',
                           target])

        self.assertIsInstance(args.func(args), CmdShowWorkflow)
        self.assertEqual(args.target, target)
        self.assertTrue(args.dvc_commits)
        self.assertTrue(args.all_commits)
        self.assertEqual(args.max_commits, 1)


class TestCheckout(TestDvc):
    def test(self):
        args = parse_args(['checkout'])
        self.assertIsInstance(args.func(args), CmdCheckout)


class TestFsck(TestDvc):
    def test(self):
        target1 = 'target1'
        target2 = 'target2'

        args = parse_args(['fsck',
                           '-p', '--physical',
                           target1,
                           target2])

        self.assertIsInstance(args.func(args), CmdFsck)
        self.assertTrue(args.physical)
        self.assertEqual(args.targets, [target1, target2])
