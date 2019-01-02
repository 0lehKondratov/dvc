import posixpath

try:
    from urlparse import urlparse
except ImportError:
    from urllib.parse import urlparse

from dvc.dependency.s3 import DependencyS3
from dvc.remote.gs import RemoteGS


class DependencyGS(DependencyS3):
    REGEX = RemoteGS.REGEX

    def __init__(self, stage, path, info=None, remote=None):
        super(DependencyGS, self).__init__(stage, path, info=info)
        self.remote = remote if remote else RemoteGS(stage.project, {})
        bucket = remote.bucket if remote else urlparse(path).netloc
        path = urlparse(path).path.lstrip('/')
        if remote:
            path = posixpath.join(remote.prefix, path)
        self.path_info = {'scheme': 'gs',
                          'bucket': bucket,
                          'path': path}
