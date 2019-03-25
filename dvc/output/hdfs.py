from __future__ import unicode_literals

import posixpath

from dvc.utils.compat import urlparse
from dvc.output.base import OutputBase
from dvc.remote.hdfs import RemoteHDFS


class OutputHDFS(OutputBase):
    REMOTE = RemoteHDFS

    def __init__(
        self,
        stage,
        path,
        info=None,
        remote=None,
        cache=True,
        metric=False,
        persist=False,
    ):
        super(OutputHDFS, self).__init__(
            stage,
            path,
            info=info,
            remote=remote,
            cache=cache,
            metric=metric,
            persist=persist,
        )
        if remote:
            path = posixpath.join(remote.url, urlparse(path).path.lstrip("/"))
        user = remote.user if remote else self.group("user")
        self.path_info = {"scheme": "hdfs", "user": user, "path": path}
