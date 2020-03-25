import os
import yaml
from collections import defaultdict

import dpath.util
from voluptuous import Any

from dvc.compat import fspath_py35
from dvc.dependency.local import DependencyLOCAL
from dvc.exceptions import DvcException


class MissingParamsError(DvcException):
    pass


class BadParamFileError(DvcException):
    pass


class DependencyPARAMS(DependencyLOCAL):
    PARAM_PARAMS = "params"
    PARAM_SCHEMA = {PARAM_PARAMS: Any(dict, list, None)}
    DEFAULT_PARAMS_FILE = "params.yaml"

    def __init__(self, stage, path, params):
        info = {}
        self.params = []
        if params:
            if isinstance(params, list):
                self.params = params
            else:
                assert isinstance(params, dict)
                self.params = list(params.keys())
                info = params

        super().__init__(
            stage,
            path
            or os.path.join(stage.repo.root_dir, self.DEFAULT_PARAMS_FILE),
            info=info,
        )

    def save(self):
        super().save()
        self.info = self.save_info()

    def status(self):
        status = super().status()

        if status[str(self)] == "deleted":
            return status

        status = defaultdict(dict)
        info = self._get_info()
        for param in self.params:
            if param not in info.keys():
                st = "deleted"
            elif param not in self.info:
                st = "new"
            elif info[param] != self.info[param]:
                st = "modified"
            else:
                assert info[param] == self.info[param]
                continue

            status[str(self)][param] = st

        return status

    def dumpd(self):
        return {
            self.PARAM_PATH: self.def_path,
            self.PARAM_PARAMS: self.info or self.params,
        }

    def _get_info(self):
        if not self.exists:
            return {}

        with open(fspath_py35(self.path_info), "r") as fobj:
            try:
                config = yaml.safe_load(fobj)
            except yaml.YAMLError as exc:
                raise BadParamFileError(
                    "Unable to read parameters from '{}'".format(self)
                ) from exc

        ret = {}
        for param in self.params:
            ret[param] = dpath.util.get(config, param, separator=".")
        return ret

    def save_info(self):
        info = self._get_info()

        missing_params = set(self.params) - set(info.keys())
        if missing_params:
            raise MissingParamsError(
                "Parameters '{}' are missing from '{}'.".format(
                    ", ".join(missing_params), self,
                )
            )

        return info
