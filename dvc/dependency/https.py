from ..tree.https import HTTPSRemoteTree
from .http import HTTPDependency


class HTTPSDependency(HTTPDependency):
    TREE_CLS = HTTPSRemoteTree
