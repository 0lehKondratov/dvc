from dvc.dependency.hdfs import DependencyHDFS

from tests.unit.dependency.local import TestDependencyLOCAL


class TestDependencyHDFS(TestDependencyLOCAL):
    def _get_cls(self):
        return DependencyHDFS
