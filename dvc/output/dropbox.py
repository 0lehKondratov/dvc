from dvc.output.base import BaseOutput

from ..tree.dropbox import DropboxTree


class DropboxOutput(BaseOutput):
    TREE_CLS = DropboxTree
