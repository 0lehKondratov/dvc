from dvc.dependency.s3 import DependencyS3

from tests.unit.dependency.local import TestDependencyLOCAL


class TestDependencyS3(TestDependencyLOCAL):
    def _get_cls(self):
        return DependencyS3
