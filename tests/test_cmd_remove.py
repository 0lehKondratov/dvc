import os

from dvc.command.remove import CmdRemove
from tests.basic_env import DirHierarchyEnvironment

class TestCmdRemove(DirHierarchyEnvironment):
    def setUp(self):
        DirHierarchyEnvironment.init_environment(self)

    def test_file_by_file_removal(self):
        self.settings.parse_args('remove --keep-in-cloud')
        cmd = CmdRemove(self.settings)

        dir1_dvc_name = os.path.join('data', self.dir1)
        self.assertTrue(os.path.exists(dir1_dvc_name))

        cmd.remove_dir(dir1_dvc_name)
        self.assertFalse(os.path.exists(dir1_dvc_name))

    def test_remove_deep_dir(self):
        self.settings.parse_args('remove --keep-in-cloud --recursive')
        cmd = CmdRemove(self.settings)

        dir1_dvc_name = os.path.join('data', self.dir1)
        self.assertTrue(os.path.exists(dir1_dvc_name))

        cmd.remove_dir(dir1_dvc_name)
        self.assertFalse(os.path.exists(dir1_dvc_name))

    def test_not_recursive_removal(self):
        self.settings.parse_args('remove --keep-in-cloud')
        cmd = CmdRemove(self.settings)

        dir1_dvc_name = os.path.join('data', self.dir1)
        self.assertTrue(os.path.exists(dir1_dvc_name))

        cmd.remove_dir(dir1_dvc_name)
        pass

    def test_data_dir_removal(self):
        self.settings.parse_args('remove --keep-in-cloud --recursive')
        cmd = CmdRemove(self.settings)

        data_dir = 'data'
        self.assertTrue(os.path.exists(data_dir))

        cmd.remove_dir(data_dir)
        pass


class TestRemoveDataItem(DirHierarchyEnvironment):
    def setUp(self):
        DirHierarchyEnvironment.init_environment(self)

    def test_remove_data_instance(self):
        self.settings.parse_args('remove --keep-in-cloud')
        cmd = CmdRemove(self.settings)

        self.assertTrue(os.path.isfile(self.file1))
        self.assertTrue(os.path.isfile(self.cache1))
        self.assertTrue(os.path.isfile(self.state1))

        cmd.remove_file(self.file1)
        self.assertFalse(os.path.exists(self.file1))
        self.assertFalse(os.path.exists(self.cache1))
        self.assertFalse(os.path.exists(self.state1))

    def test_keep_cache(self):
        self.settings.parse_args('remove --keep-in-cloud --keep-in-cache')
        cmd = CmdRemove(self.settings)

        self.assertTrue(os.path.isfile(self.file1))
        self.assertTrue(os.path.isfile(self.cache1))
        self.assertTrue(os.path.isfile(self.state1))

        cmd.remove_file(self.file1)
        self.assertFalse(os.path.exists(self.file1))
        self.assertTrue(os.path.exists(self.cache1))
        self.assertFalse(os.path.exists(self.state1))

    def test_remove_data_instance_without_cache(self):
        self.settings.parse_args('remove --keep-in-cloud')
        cmd = CmdRemove(self.settings)

        self.assertTrue(os.path.isfile(self.file6))
        self.assertIsNone(self.cache6)
        self.assertTrue(os.path.exists(self.state6))
        self.assertTrue(os.path.isfile(self.state6))

        cmd.remove_file(self.file6)
        self.assertFalse(os.path.exists(self.file6))


class TestRemoveEndToEnd(DirHierarchyEnvironment):
    def setUp(self):
        DirHierarchyEnvironment.init_environment(self)

    def test_end_to_end_with_no_error(self):
        dir11_full = os.path.join('data', self.dir11)
        dir2_full = os.path.join('data', self.dir2)

        self.settings.parse_args('remove --no-git-actions --keep-in-cloud --recursive {} {}'.format(dir11_full, self.file5))
        cmd = CmdRemove(self.settings)

        self.assertTrue(os.path.exists(dir11_full))
        self.assertTrue(os.path.exists(self.file5))
        self.assertTrue(os.path.exists(dir2_full))
        self.assertTrue(os.path.exists(self.file1))

        self.assertTrue(cmd.remove_all())

        self.assertFalse(os.path.exists(dir11_full))
        self.assertFalse(os.path.exists(self.file5))
        self.assertTrue(os.path.exists(dir2_full))
        self.assertTrue(os.path.exists(self.file1))

    def test_run(self):
        dir11_full = os.path.join('data', self.dir11)
        dir2_full = os.path.join('data', self.dir2)

        self.settings.parse_args('remove --no-git-actions --keep-in-cloud --recursive {} {}'.format(dir11_full, self.file5))
        cmd = CmdRemove(self.settings)

        self.assertTrue(os.path.exists(dir11_full))
        self.assertTrue(os.path.exists(self.file5))
        self.assertTrue(os.path.exists(dir2_full))
        self.assertTrue(os.path.exists(self.file1))

        self.assertEqual(cmd.run(), 0)

        self.assertFalse(os.path.exists(dir11_full))
        self.assertFalse(os.path.exists(self.file5))
        self.assertTrue(os.path.exists(dir2_full))
        self.assertTrue(os.path.exists(self.file1))


class TestRemoveEndToEndWithAnError(DirHierarchyEnvironment):
    def setUp(self):
        DirHierarchyEnvironment.init_environment(self)

    def test(self):
        dir11_full = os.path.join(self._config.data_dir, self.dir11)
        dir2_full = os.path.join(self._config.data_dir, self.dir2)

        self.settings.parse_args('remove --no-git-actions --keep-in-cloud --recursive {} {}'.format(dir11_full, self.file5))
        cmd = CmdRemove(self.settings)

        self.assertTrue(os.path.exists(dir11_full))
        self.assertTrue(os.path.exists(self.file5))
        self.assertTrue(os.path.exists(dir2_full))
        self.assertTrue(os.path.exists(self.file1))
        self.assertTrue(os.path.exists(self.file6))

        self.assertTrue(cmd.remove_all())

        self.assertFalse(os.path.exists(dir11_full))
        self.assertFalse(os.path.exists(self.file5))
        self.assertTrue(os.path.exists(dir2_full))
        self.assertTrue(os.path.exists(self.file1))
