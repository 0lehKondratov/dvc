from ..fs import get_cloud_fs
from .base import Remote
from .local import LocalRemote


def get_remote(repo, **kwargs):
    cls, config = get_cloud_fs(repo, **kwargs)
    fs = cls(**config)
    if fs.scheme == "local":
        return LocalRemote(fs, repo.tmp_dir, **config)
    return Remote(fs, repo.tmp_dir, **config)
