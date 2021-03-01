import base64
import threading

from funcy import cached_property, wrap_prop

from dvc.path_info import CloudURLInfo
from dvc.scheme import Schemes

from .fsspec_wrapper import FSSpecWrapper


class GSFileSystem(FSSpecWrapper):
    scheme = Schemes.GS
    PATH_CLS = CloudURLInfo
    REQUIRES = {"gcsfs": "gcsfs"}
    PARAM_CHECKSUM = "md5"
    DETAIL_FIELDS = frozenset(("md5", "size"))

    def __init__(self, repo, config):
        super().__init__(repo, config)

        url = config.get("url", "gs://")
        self.path_info = self.PATH_CLS(url)

        self.login_info = self._prepare_credentials(config)

    def _prepare_credentials(self, config):
        login_info = {}
        login_info["project"] = config.get("projectname")
        login_info["token"] = config.get("credentialpath")
        return login_info

    def _entry_hook(self, entry):
        if "md5Hash" in entry:
            entry["md5"] = base64.b64decode(entry["md5Hash"]).hex()
        return entry

    @wrap_prop(threading.Lock())
    @cached_property
    def fs(self):
        from gcsfs import GCSFileSystem

        return GCSFileSystem(**self.login_info)
