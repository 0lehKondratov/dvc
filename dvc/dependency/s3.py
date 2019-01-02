import posixpath

try:
    from urlparse import urlparse
except ImportError:
    from urllib.parse import urlparse

from dvc.dependency.base import DependencyBase
from dvc.remote.s3 import RemoteS3


class DependencyS3(DependencyBase):
    REGEX = RemoteS3.REGEX

    def __init__(self, stage, path, info=None, remote=None):
        super(DependencyS3, self).__init__(stage, path, info=info)
        self.remote = remote or RemoteS3(stage.project, {})

        bucket = remote.bucket if remote else urlparse(path).netloc
        path = urlparse(path).path.lstrip('/')
        if remote:
            path = posixpath.join(remote.prefix, path)
        self.path_info = {'scheme': 's3',
                          'bucket': bucket,
                          'path': path}

    def changed(self):
        if not self.exists:
            return True

        return self.info != self.remote.save_info(self.path_info)

    def save(self):
        self.info = self.remote.save_info(self.path_info)
