from builtins import open as builtin_open
import importlib
import os
import sys
from urllib.parse import urlparse
from contextlib import contextmanager, _GeneratorContextManager as GCM
import threading

from funcy import wrap_with
import ruamel.yaml
from voluptuous import Schema, Required, Invalid

from dvc.repo import Repo
from dvc.exceptions import DvcException
from dvc.external_repo import external_repo


SUMMON_FILE_SCHEMA = Schema(
    {
        Required("objects"): [
            {
                Required("name"): str,
                "meta": dict,
                Required("summon"): {
                    Required("type"): str,
                    "deps": [str],
                    str: object,
                },
            }
        ]
    }
)
SUMMON_PYTHON_SCHEMA = Schema(
    {
        Required("type"): "python",
        Required("call"): str,
        "args": dict,
        "deps": [str],
    }
)


class SummonError(DvcException):
    pass


def get_url(path, repo=None, rev=None, remote=None):
    """Returns an url of a resource specified by path in repo"""
    with _make_repo(repo, rev=rev) as _repo:
        abspath = os.path.join(_repo.root_dir, path)
        out, = _repo.find_outs_by_path(abspath)
        remote_obj = _repo.cloud.get_remote(remote)
        return str(remote_obj.checksum_to_path_info(out.checksum))


def open(path, repo=None, rev=None, remote=None, mode="r", encoding=None):
    """Opens a specified resource as a file descriptor"""
    args = (path,)
    kwargs = {
        "repo": repo,
        "remote": remote,
        "rev": rev,
        "mode": mode,
        "encoding": encoding,
    }
    return _OpenContextManager(_open, args, kwargs)


class _OpenContextManager(GCM):
    def __init__(self, func, args, kwds):
        self.gen = func(*args, **kwds)
        self.func, self.args, self.kwds = func, args, kwds

    def __getattr__(self, name):
        raise AttributeError(
            "dvc.api.open() should be used in a with statement"
        )


def _open(path, repo=None, rev=None, remote=None, mode="r", encoding=None):
    with _make_repo(repo, rev=rev) as _repo:
        abspath = os.path.join(_repo.root_dir, path)
        with _repo.open(
            abspath, remote=remote, mode=mode, encoding=encoding
        ) as fd:
            yield fd


def read(path, repo=None, rev=None, remote=None, mode="r", encoding=None):
    """Read a specified resource into string"""
    with open(
        path, repo=repo, rev=rev, remote=remote, mode=mode, encoding=encoding
    ) as fd:
        return fd.read()


@contextmanager
def _make_repo(repo_url, rev=None):
    if not repo_url or urlparse(repo_url).scheme == "":
        assert rev is None, "Custom revision is not supported for local repo"
        yield Repo(repo_url)
    else:
        with external_repo(url=repo_url, rev=rev) as repo:
            yield repo


def summon(name, repo=None, rev=None, summon_file="dvcsummon.yaml", args=None):
    """Instantiate an object described in the summon file."""
    with prepare_summon(
        name, repo=repo, rev=rev, summon_file=summon_file
    ) as desc:
        try:
            summon_dict = SUMMON_PYTHON_SCHEMA(desc.obj["summon"])
        except Invalid as exc:
            raise SummonError(str(exc)) from exc

        _args = {**summon_dict.get("args", {}), **(args or {})}
        return _invoke_method(summon_dict["call"], _args, desc.repo.root_dir)


@contextmanager
def prepare_summon(name, repo=None, rev=None, summon_file="dvcsummon.yaml"):
    """Does a couple of things every summon needs as a prerequisite:
    clones the repo, parses the summon file and pulls the deps.

    Calling code is expected to complete the summon logic following
    instructions stated in "summon" dict of the object spec.

    Returns a SummonDesc instance, which contains references to a Repo object,
    named object specification and resolved paths to deps.
    """
    with _make_repo(repo, rev=rev) as _repo:
        try:
            path = os.path.join(_repo.root_dir, summon_file)
            obj = _get_object_spec(name, path)
            yield SummonDesc(_repo, obj)
        except SummonError as exc:
            raise SummonError(
                str(exc) + " at '{}' in '{}'".format(summon_file, repo)
            ) from exc.__cause__


class SummonDesc:
    def __init__(self, repo, obj):
        self.repo = repo
        self.obj = obj
        self._pull_deps()

    @property
    def deps(self):
        return [os.path.join(self.repo.root_dir, d) for d in self._deps]

    @property
    def _deps(self):
        return self.obj["summon"].get("deps", [])

    def _pull_deps(self):
        if not self._deps:
            return

        outs = [self.repo.find_out_by_relpath(d) for d in self._deps]

        with self.repo.state:
            for out in outs:
                self.repo.cloud.pull(out.get_used_cache())
                out.checkout()


def _get_object_spec(name, path):
    """
    Given a summonable object's name, search for it on the given file
    and return its description.
    """
    try:
        with builtin_open(path, "r") as fobj:
            content = SUMMON_FILE_SCHEMA(ruamel.yaml.safe_load(fobj.read()))
            objects = [x for x in content["objects"] if x["name"] == name]

        if not objects:
            raise SummonError("No object with name '{}'".format(name))
        elif len(objects) >= 2:
            raise SummonError(
                "More than one object with name '{}'".format(name)
            )

        return objects[0]

    except FileNotFoundError as exc:
        raise SummonError("Summon file not found") from exc
    except ruamel.yaml.YAMLError as exc:
        raise SummonError("Failed to parse summon file") from exc
    except Invalid as exc:
        raise SummonError(str(exc)) from exc


@wrap_with(threading.Lock())
def _invoke_method(call, args, path):
    # XXX: Some issues with this approach:
    #   * Import will pollute sys.modules
    #   * sys.path manipulation is "theoretically" not needed,
    #     but tests are failing for an unknown reason.
    cwd = os.getcwd()

    try:
        os.chdir(path)
        sys.path.insert(0, path)
        method = _import_string(call)
        return method(**args)
    finally:
        os.chdir(cwd)
        sys.path.pop(0)


def _import_string(import_name):
    """Imports an object based on a string.
    Useful to delay import to not load everything on startup.
    Use dotted notaion in `import_name`, e.g. 'dvc.remote.gs.RemoteGS'.

    :return: imported object
    """
    if "." in import_name:
        module, obj = import_name.rsplit(".", 1)
    else:
        return importlib.import_module(import_name)
    return getattr(importlib.import_module(module), obj)
