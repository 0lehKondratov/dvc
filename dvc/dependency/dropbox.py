from dvc.dependency.base import BaseDependency
from dvc.output.dropbox import DropboxOutput


class DropboxDependency(BaseDependency, DropboxOutput):
    pass
