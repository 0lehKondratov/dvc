from __future__ import unicode_literals

from dvc.utils.compat import str

import copy
import re
import os
import subprocess
import logging
import signal
import threading
from itertools import chain

from dvc.utils import relpath
from dvc.utils.compat import pathlib
from dvc.utils.fs import contains_symlink_up_to
from schema import Schema, SchemaError, Optional, Or, And

import dvc.prompt as prompt
import dvc.dependency as dependency
import dvc.output as output
from dvc.exceptions import DvcException
from dvc.utils import dict_md5, fix_env
from dvc.utils.collections import apply_diff
from dvc.utils.stage import load_stage_fd, dump_stage_file


logger = logging.getLogger(__name__)


class StageCmdFailedError(DvcException):
    def __init__(self, stage):
        msg = "stage '{}' cmd '{}' failed".format(stage.relpath, stage.cmd)
        super(StageCmdFailedError, self).__init__(msg)


class StageFileFormatError(DvcException):
    def __init__(self, fname, e):
        msg = "DVC-file '{}' format error: {}".format(fname, str(e))
        super(StageFileFormatError, self).__init__(msg)


class StageFileDoesNotExistError(DvcException):
    def __init__(self, fname):
        msg = "'{}' does not exist.".format(fname)

        sname = fname + Stage.STAGE_FILE_SUFFIX
        if Stage.is_stage_file(sname):
            msg += " Do you mean '{}'?".format(sname)

        super(StageFileDoesNotExistError, self).__init__(msg)


class StageFileAlreadyExistsError(DvcException):
    def __init__(self, relpath):
        msg = "stage '{}' already exists".format(relpath)
        super(StageFileAlreadyExistsError, self).__init__(msg)


class StageFileIsNotDvcFileError(DvcException):
    def __init__(self, fname):
        msg = "'{}' is not a DVC-file".format(fname)

        sname = fname + Stage.STAGE_FILE_SUFFIX
        if Stage.is_stage_file(sname):
            msg += " Do you mean '{}'?".format(sname)

        super(StageFileIsNotDvcFileError, self).__init__(msg)


class StageFileBadNameError(DvcException):
    def __init__(self, msg):
        super(StageFileBadNameError, self).__init__(msg)


class StagePathOutsideError(DvcException):
    def __init__(self, path):
        msg = "stage working or file path '{}' is outside of dvc repo"
        super(StagePathOutsideError, self).__init__(msg.format(path))


class StagePathNotFoundError(DvcException):
    def __init__(self, path):
        msg = "stage working or file path '{}' does not exist"
        super(StagePathNotFoundError, self).__init__(msg.format(path))


class StagePathNotDirectoryError(DvcException):
    def __init__(self, path):
        msg = "stage working or file path '{}' is not directory"
        super(StagePathNotDirectoryError, self).__init__(msg.format(path))


class StageCommitError(DvcException):
    pass


class StageUpdateError(DvcException):
    def __init__(self, path):
        super(StageUpdateError, self).__init__(
            "update is not supported for '{}' that is not an "
            "import.".format(path)
        )


class MissingDep(DvcException):
    def __init__(self, deps):
        assert len(deps) > 0

        if len(deps) > 1:
            dep = "dependencies"
        else:
            dep = "dependency"

        msg = "missing {}: {}".format(dep, ", ".join(map(str, deps)))
        super(MissingDep, self).__init__(msg)


class MissingDataSource(DvcException):
    def __init__(self, missing_files):
        assert len(missing_files) > 0

        source = "source"
        if len(missing_files) > 1:
            source += "s"

        msg = "missing data {}: {}".format(source, ", ".join(missing_files))
        super(MissingDataSource, self).__init__(msg)


class Stage(object):
    STAGE_FILE = "Dvcfile"
    STAGE_FILE_SUFFIX = ".dvc"

    PARAM_MD5 = "md5"
    PARAM_CMD = "cmd"
    PARAM_WDIR = "wdir"
    PARAM_DEPS = "deps"
    PARAM_OUTS = "outs"
    PARAM_LOCKED = "locked"
    PARAM_META = "meta"
    PARAM_ALWAYS_CHANGED = "always_changed"

    SCHEMA = {
        Optional(PARAM_MD5): Or(str, None),
        Optional(PARAM_CMD): Or(str, None),
        Optional(PARAM_WDIR): Or(str, None),
        Optional(PARAM_DEPS): Or(And(list, Schema([dependency.SCHEMA])), None),
        Optional(PARAM_OUTS): Or(And(list, Schema([output.SCHEMA])), None),
        Optional(PARAM_LOCKED): bool,
        Optional(PARAM_META): object,
        Optional(PARAM_ALWAYS_CHANGED): bool,
    }

    TAG_REGEX = r"^(?P<path>.*)@(?P<tag>[^\\/@:]*)$"

    def __init__(
        self,
        repo,
        path=None,
        cmd=None,
        wdir=os.curdir,
        deps=None,
        outs=None,
        md5=None,
        locked=False,
        tag=None,
        state=None,
        always_changed=False,
    ):
        if deps is None:
            deps = []
        if outs is None:
            outs = []

        self.repo = repo
        self.path = path
        self.cmd = cmd
        self.wdir = wdir
        self.outs = outs
        self.deps = deps
        self.md5 = md5
        self.locked = locked
        self.tag = tag
        self.always_changed = always_changed
        self._state = state or {}

    def __repr__(self):
        return "Stage: '{path}'".format(
            path=self.relpath if self.path else "No path"
        )

    @property
    def relpath(self):
        return relpath(self.path)

    @property
    def is_data_source(self):
        """Whether the DVC-file was created with `dvc add` or `dvc import`"""
        return self.cmd is None

    @staticmethod
    def is_valid_filename(path):
        return (
            # path.endswith doesn't work for encoded unicode filenames on
            # Python 2 and since Stage.STAGE_FILE_SUFFIX is ascii then it is
            # not needed to decode the path from py2's str
            path[-len(Stage.STAGE_FILE_SUFFIX) :] == Stage.STAGE_FILE_SUFFIX
            or os.path.basename(path) == Stage.STAGE_FILE
        )

    @staticmethod
    def is_stage_file(path):
        return os.path.isfile(path) and Stage.is_valid_filename(path)

    def changed_md5(self):
        return self.md5 != self._compute_md5()

    @property
    def is_callback(self):
        """
        A callback stage is always considered as changed,
        so it runs on every `dvc repro` call.
        """
        return not self.is_data_source and len(self.deps) == 0

    @property
    def is_import(self):
        """Whether the DVC-file was created with `dvc import`."""
        return not self.cmd and len(self.deps) == 1 and len(self.outs) == 1

    @property
    def is_repo_import(self):
        if not self.is_import:
            return False

        return isinstance(self.deps[0], dependency.DependencyREPO)

    def _changed_deps(self):
        if self.locked:
            return False

        if self.is_callback:
            logger.warning(
                "DVC-file '{fname}' is a 'callback' stage "
                "(has a command and no dependencies) and thus always "
                "considered as changed.".format(fname=self.relpath)
            )
            return True

        if self.always_changed:
            return True

        for dep in self.deps:
            status = dep.status()
            if status:
                logger.warning(
                    "Dependency '{dep}' of '{stage}' changed because it is "
                    "'{status}'.".format(
                        dep=dep, stage=self.relpath, status=status[str(dep)]
                    )
                )
                return True

        return False

    def _changed_outs(self):
        for out in self.outs:
            status = out.status()
            if status:
                logger.warning(
                    "Output '{out}' of '{stage}' changed because it is "
                    "'{status}'".format(
                        out=out, stage=self.relpath, status=status[str(out)]
                    )
                )
                return True

        return False

    def _changed_md5(self):
        if self.changed_md5():
            logger.warning("DVC-file '{}' changed.".format(self.relpath))
            return True
        return False

    def changed(self):
        # Short-circuit order: stage md5 is fast, deps are expected to change
        ret = (
            self._changed_md5() or self._changed_deps() or self._changed_outs()
        )

        if ret:
            logger.warning("Stage '{}' changed.".format(self.relpath))
        else:
            logger.debug("Stage '{}' didn't change.".format(self.relpath))

        return ret

    def remove_outs(self, ignore_remove=False, force=False):
        """Used mainly for `dvc remove --outs` and :func:`Stage.reproduce`."""
        for out in self.outs:
            if out.persist and not force:
                out.unprotect()
            else:
                logger.debug(
                    "Removing output '{out}' of '{stage}'.".format(
                        out=out, stage=self.relpath
                    )
                )
                out.remove(ignore_remove=ignore_remove)

    def unprotect_outs(self):
        for out in self.outs:
            out.unprotect()

    def remove(self, force=False, remove_outs=True):
        if remove_outs:
            self.remove_outs(ignore_remove=True, force=force)
        else:
            self.unprotect_outs()
        os.unlink(self.path)

    def reproduce(self, interactive=False, **kwargs):

        if not kwargs.get("force", False) and not self.changed():
            return None

        msg = (
            "Going to reproduce '{stage}'. "
            "Are you sure you want to continue?".format(stage=self.relpath)
        )

        if interactive and not prompt.confirm(msg):
            raise DvcException("reproduction aborted by the user")

        self.run(**kwargs)

        logger.debug("'{stage}' was reproduced".format(stage=self.relpath))

        return self

    def update(self):
        if not self.is_repo_import and not self.is_import:
            raise StageUpdateError(self.relpath)

        self.deps[0].update()
        locked = self.locked
        self.locked = False
        try:
            self.reproduce()
        finally:
            self.locked = locked

    @staticmethod
    def validate(d, fname=None):
        from dvc.utils import convert_to_unicode

        try:
            Schema(Stage.SCHEMA).validate(convert_to_unicode(d))
        except SchemaError as exc:
            raise StageFileFormatError(fname, exc)

    @classmethod
    def _stage_fname(cls, outs, add):
        if not outs:
            return cls.STAGE_FILE

        out = outs[0]
        fname = out.path_info.name + cls.STAGE_FILE_SUFFIX

        if (
            add
            and out.is_in_repo
            and not contains_symlink_up_to(out.fspath, out.repo.root_dir)
        ):
            fname = out.path_info.with_name(fname).fspath

        return fname

    @staticmethod
    def _check_stage_path(repo, path):
        assert repo is not None

        real_path = os.path.realpath(path)
        if not os.path.exists(real_path):
            raise StagePathNotFoundError(path)

        if not os.path.isdir(real_path):
            raise StagePathNotDirectoryError(path)

        proj_dir = os.path.realpath(repo.root_dir) + os.path.sep
        if not (real_path + os.path.sep).startswith(proj_dir):
            raise StagePathOutsideError(path)

    @property
    def is_cached(self):
        """
        Checks if this stage has been already ran and stored
        """
        from dvc.remote.local import RemoteLOCAL
        from dvc.remote.s3 import RemoteS3

        old = Stage.load(self.repo, self.path)
        if old._changed_outs():
            return False

        # NOTE: need to save checksums for deps in order to compare them
        # with what is written in the old stage.
        for dep in self.deps:
            dep.save()

        old_d = old.dumpd()
        new_d = self.dumpd()

        # NOTE: need to remove checksums from old dict in order to compare
        # it to the new one, since the new one doesn't have checksums yet.
        old_d.pop(self.PARAM_MD5, None)
        new_d.pop(self.PARAM_MD5, None)
        outs = old_d.get(self.PARAM_OUTS, [])
        for out in outs:
            out.pop(RemoteLOCAL.PARAM_CHECKSUM, None)
            out.pop(RemoteS3.PARAM_CHECKSUM, None)

        if old_d != new_d:
            return False

        # NOTE: committing to prevent potential data duplication. For example
        #
        #    $ dvc config cache.type hardlink
        #    $ echo foo > foo
        #    $ dvc add foo
        #    $ rm -f foo
        #    $ echo foo > foo
        #    $ dvc add foo # should replace foo with a link to cache
        #
        old.commit()

        return True

    @staticmethod
    def create(repo, **kwargs):

        wdir = kwargs.get("wdir", None)
        cwd = kwargs.get("cwd", None)
        fname = kwargs.get("fname", None)
        add = kwargs.get("add", False)

        # Backward compatibility for `cwd` option
        if wdir is None and cwd is not None:
            if fname is not None and os.path.basename(fname) != fname:
                raise StageFileBadNameError(
                    "DVC-file name '{fname}' may not contain subdirectories"
                    " if '-c|--cwd' (deprecated) is specified. Use '-w|--wdir'"
                    " along with '-f' to specify DVC-file path with working"
                    " directory.".format(fname=fname)
                )
            wdir = cwd
        elif wdir is None:
            wdir = os.curdir

        stage = Stage(
            repo=repo,
            wdir=wdir,
            cmd=kwargs.get("cmd", None),
            locked=kwargs.get("locked", False),
            always_changed=kwargs.get("always_changed", False),
        )

        Stage._fill_stage_outputs(stage, **kwargs)
        stage.deps = dependency.loads_from(
            stage, kwargs.get("deps", []), erepo=kwargs.get("erepo", None)
        )

        stage._check_circular_dependency()
        stage._check_duplicated_arguments()

        if not fname:
            fname = Stage._stage_fname(stage.outs, add)
        stage._check_dvc_filename(fname)

        # Autodetecting wdir for add, we need to create outs first to do that,
        # so we start with wdir = . and remap out paths later.
        if add and kwargs.get("wdir") is None and cwd is None:
            wdir = os.path.dirname(fname)

            for out in chain(stage.outs, stage.deps):
                if out.is_in_repo:
                    out.def_path = relpath(out.path_info, wdir)

        wdir = os.path.abspath(wdir)

        if cwd is not None:
            path = os.path.join(wdir, fname)
        else:
            path = os.path.abspath(fname)

        Stage._check_stage_path(repo, wdir)
        Stage._check_stage_path(repo, os.path.dirname(path))

        stage.wdir = wdir
        stage.path = path

        ignore_build_cache = kwargs.get("ignore_build_cache", False)

        # NOTE: remove outs before we check build cache
        if kwargs.get("remove_outs", False):
            logger.warning(
                "--remove-outs is deprecated."
                " It is now the default behavior,"
                " so there's no need to use this option anymore."
            )
            stage.remove_outs(ignore_remove=False)
            logger.warning("Build cache is ignored when using --remove-outs.")
            ignore_build_cache = True

        if os.path.exists(path) and any(out.persist for out in stage.outs):
            logger.warning("Build cache is ignored when persisting outputs.")
            ignore_build_cache = True

        if os.path.exists(path):
            if (
                not ignore_build_cache
                and stage.is_cached
                and not stage.is_callback
                and not stage.always_changed
            ):
                logger.info("Stage is cached, skipping.")
                return None

            msg = (
                "'{}' already exists. Do you wish to run the command and "
                "overwrite it?".format(stage.relpath)
            )

            if not kwargs.get("overwrite", True) and not prompt.confirm(msg):
                raise StageFileAlreadyExistsError(stage.relpath)

            os.unlink(path)

        return stage

    @staticmethod
    def _fill_stage_outputs(stage, **kwargs):
        stage.outs = output.loads_from(
            stage, kwargs.get("outs", []), use_cache=True
        )
        stage.outs += output.loads_from(
            stage, kwargs.get("metrics", []), use_cache=True, metric=True
        )
        stage.outs += output.loads_from(
            stage, kwargs.get("outs_persist", []), use_cache=True, persist=True
        )
        stage.outs += output.loads_from(
            stage, kwargs.get("outs_no_cache", []), use_cache=False
        )
        stage.outs += output.loads_from(
            stage,
            kwargs.get("metrics_no_cache", []),
            use_cache=False,
            metric=True,
        )
        stage.outs += output.loads_from(
            stage,
            kwargs.get("outs_persist_no_cache", []),
            use_cache=False,
            persist=True,
        )

    @staticmethod
    def _check_dvc_filename(fname):
        if not Stage.is_valid_filename(fname):
            raise StageFileBadNameError(
                "bad DVC-file name '{}'. DVC-files should be named"
                " 'Dvcfile' or have a '.dvc' suffix (e.g. '{}.dvc').".format(
                    relpath(fname), os.path.basename(fname)
                )
            )

    @staticmethod
    def _check_file_exists(repo, fname):
        if not repo.tree.exists(fname):
            raise StageFileDoesNotExistError(fname)

    @staticmethod
    def _check_isfile(repo, fname):
        if not repo.tree.isfile(fname):
            raise StageFileIsNotDvcFileError(fname)

    @classmethod
    def _get_path_tag(cls, s):
        regex = re.compile(cls.TAG_REGEX)
        match = regex.match(s)
        if not match:
            return s, None
        return match.group("path"), match.group("tag")

    @staticmethod
    def load(repo, fname):
        fname, tag = Stage._get_path_tag(fname)

        # it raises the proper exceptions by priority:
        # 1. when the file doesn't exists
        # 2. filename is not a DVC-file
        # 3. path doesn't represent a regular file
        Stage._check_file_exists(repo, fname)
        Stage._check_dvc_filename(fname)
        Stage._check_isfile(repo, fname)

        with repo.tree.open(fname) as fd:
            d = load_stage_fd(fd, fname)
        # Making a deepcopy since the original structure
        # looses keys in deps and outs load
        state = copy.deepcopy(d)

        Stage.validate(d, fname=relpath(fname))
        path = os.path.abspath(fname)

        stage = Stage(
            repo=repo,
            path=path,
            wdir=os.path.abspath(
                os.path.join(
                    os.path.dirname(path), d.get(Stage.PARAM_WDIR, ".")
                )
            ),
            cmd=d.get(Stage.PARAM_CMD),
            md5=d.get(Stage.PARAM_MD5),
            locked=d.get(Stage.PARAM_LOCKED, False),
            tag=tag,
            always_changed=d.get(Stage.PARAM_ALWAYS_CHANGED, False),
            state=state,
        )

        stage.deps = dependency.loadd_from(stage, d.get(Stage.PARAM_DEPS, []))
        stage.outs = output.loadd_from(stage, d.get(Stage.PARAM_OUTS, []))

        return stage

    def dumpd(self):
        rel_wdir = relpath(self.wdir, os.path.dirname(self.path))

        wdir = pathlib.PurePath(rel_wdir).as_posix()
        wdir = wdir if wdir != "." else None

        return {
            key: value
            for key, value in {
                Stage.PARAM_MD5: self.md5,
                Stage.PARAM_CMD: self.cmd,
                Stage.PARAM_WDIR: wdir,
                Stage.PARAM_LOCKED: self.locked,
                Stage.PARAM_DEPS: [d.dumpd() for d in self.deps],
                Stage.PARAM_OUTS: [o.dumpd() for o in self.outs],
                Stage.PARAM_META: self._state.get("meta"),
                Stage.PARAM_ALWAYS_CHANGED: self.always_changed,
            }.items()
            if value
        }

    def dump(self):
        fname = self.path

        self._check_dvc_filename(fname)

        logger.debug(
            "Saving information to '{file}'.".format(file=relpath(fname))
        )
        d = self.dumpd()
        apply_diff(d, self._state)
        dump_stage_file(fname, self._state)

        self.repo.scm.track_file(relpath(fname))

    def _compute_md5(self):
        from dvc.output.base import OutputBase

        d = self.dumpd()

        # Remove md5 and meta, these should not affect stage md5
        d.pop(self.PARAM_MD5, None)
        d.pop(self.PARAM_META, None)

        # Ignore the wdir default value. In this case DVC-file w/o
        # wdir has the same md5 as a file with the default value specified.
        # It's important for backward compatibility with pipelines that
        # didn't have WDIR in their DVC-files.
        if d.get(self.PARAM_WDIR) == ".":
            del d[self.PARAM_WDIR]

        # NOTE: excluding parameters that don't affect the state of the
        # pipeline. Not excluding `OutputLOCAL.PARAM_CACHE`, because if
        # it has changed, we might not have that output in our cache.
        m = dict_md5(
            d,
            exclude=[
                self.PARAM_LOCKED,
                OutputBase.PARAM_METRIC,
                OutputBase.PARAM_TAGS,
                OutputBase.PARAM_PERSIST,
            ],
        )
        logger.debug("Computed stage '{}' md5: '{}'".format(self.relpath, m))
        return m

    def save(self):
        for dep in self.deps:
            dep.save()

        for out in self.outs:
            out.save()

        self.md5 = self._compute_md5()

    @staticmethod
    def _changed_entries(entries):
        return [
            str(entry)
            for entry in entries
            if entry.checksum and entry.changed_checksum()
        ]

    def check_can_commit(self, force):
        changed_deps = self._changed_entries(self.deps)
        changed_outs = self._changed_entries(self.outs)

        if changed_deps or changed_outs or self.changed_md5():
            msg = (
                "dependencies {}".format(changed_deps) if changed_deps else ""
            )
            msg += " and " if (changed_deps and changed_outs) else ""
            msg += "outputs {}".format(changed_outs) if changed_outs else ""
            msg += "md5" if not (changed_deps or changed_outs) else ""
            msg += " of '{}' changed.".format(self.relpath)
            msg += "Are you sure you want to commit it?"
            if not force and not prompt.confirm(msg):
                raise StageCommitError(
                    "unable to commit changed '{}'. Use `-f|--force` to "
                    "force.`".format(self.relpath)
                )
            self.save()

    def commit(self):
        for out in self.outs:
            out.commit()

    def _check_missing_deps(self):
        missing = [dep for dep in self.deps if not dep.exists]

        if any(missing):
            raise MissingDep(missing)

    @staticmethod
    def _warn_if_fish(executable):  # pragma: no cover
        if (
            executable is None
            or os.path.basename(os.path.realpath(executable)) != "fish"
        ):
            return

        logger.warning(
            "DVC detected that you are using fish as your default "
            "shell. Be aware that it might cause problems by overwriting "
            "your current environment variables with values defined "
            "in '.fishrc', which might affect your command. See "
            "https://github.com/iterative/dvc/issues/1307. "
        )

    def _check_circular_dependency(self):
        from dvc.exceptions import CircularDependencyError

        circular_dependencies = set(d.path_info for d in self.deps) & set(
            o.path_info for o in self.outs
        )

        if circular_dependencies:
            raise CircularDependencyError(str(circular_dependencies.pop()))

    def _check_duplicated_arguments(self):
        from dvc.exceptions import ArgumentDuplicationError
        from collections import Counter

        path_counts = Counter(edge.path_info for edge in self.deps + self.outs)

        for path, occurrence in path_counts.items():
            if occurrence > 1:
                raise ArgumentDuplicationError(str(path))

    def _run(self):
        self._check_missing_deps()

        kwargs = {"cwd": self.wdir, "env": fix_env(None), "close_fds": True}

        if os.name == "nt":
            kwargs["shell"] = True
            cmd = self.cmd
        else:
            # NOTE: when you specify `shell=True`, `Popen` [1] will default to
            # `/bin/sh` on *nix and will add ["/bin/sh", "-c"] to your command.
            # But we actually want to run the same shell that we are running
            # from right now, which is usually determined by the `SHELL` env
            # var. So instead, we compose our command on our own, making sure
            # to include special flags to prevent shell from reading any
            # configs and modifying env, which may change the behavior or the
            # command we are running. See [2] for more info.
            #
            # [1] https://github.com/python/cpython/blob/3.7/Lib/subprocess.py
            #                                                            #L1426
            # [2] https://github.com/iterative/dvc/issues/2506
            #                                           #issuecomment-535396799
            kwargs["shell"] = False
            executable = os.getenv("SHELL") or "/bin/sh"

            self._warn_if_fish(executable)

            opts = {"zsh": ["--no-rcs"], "bash": ["--noprofile", "--norc"]}
            name = os.path.basename(executable).lower()
            cmd = [executable] + opts.get(name, []) + ["-c", self.cmd]

        main_thread = isinstance(
            threading.current_thread(), threading._MainThread
        )
        old_handler = None
        p = None

        try:
            p = subprocess.Popen(cmd, **kwargs)
            if main_thread:
                old_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
            p.communicate()
        finally:
            if old_handler:
                signal.signal(signal.SIGINT, old_handler)

        if (p is None) or (p.returncode != 0):
            raise StageCmdFailedError(self)

    def run(self, dry=False, no_commit=False, force=False):
        if (self.cmd or self.is_import) and not self.locked and not dry:
            self.remove_outs(ignore_remove=False, force=False)

        if self.locked:
            logger.info(
                "Verifying outputs in locked stage '{stage}'".format(
                    stage=self.relpath
                )
            )
            if not dry:
                self.check_missing_outputs()

        elif self.is_import:
            logger.info(
                "Importing '{dep}' -> '{out}'".format(
                    dep=self.deps[0], out=self.outs[0]
                )
            )
            if not dry:
                if not force and self._already_cached():
                    self.outs[0].checkout()
                else:
                    self.deps[0].download(self.outs[0])

        elif self.is_data_source:
            msg = "Verifying data sources in '{}'".format(self.relpath)
            logger.info(msg)
            if not dry:
                self.check_missing_outputs()

        else:
            logger.info("Running command:\n\t{}".format(self.cmd))
            if not dry:
                if (
                    not force
                    and not self.is_callback
                    and not self.always_changed
                    and self._already_cached()
                ):
                    self.checkout()
                else:
                    self._run()

        if not dry:
            self.save()
            if not no_commit:
                self.commit()

    def check_missing_outputs(self):
        paths = [str(out) for out in self.outs if not out.exists]
        if paths:
            raise MissingDataSource(paths)

    def checkout(self, force=False, progress_callback=None):
        failed_checkouts = []
        for out in self.outs:
            failed = out.checkout(
                force=force, tag=self.tag, progress_callback=progress_callback
            )
            if failed:
                failed_checkouts.append(failed)
        return failed_checkouts

    @staticmethod
    def _status(entries):
        ret = {}

        for entry in entries:
            ret.update(entry.status())

        return ret

    def status(self):
        ret = []

        if not self.locked:
            deps_status = self._status(self.deps)
            if deps_status:
                ret.append({"changed deps": deps_status})

        outs_status = self._status(self.outs)
        if outs_status:
            ret.append({"changed outs": outs_status})

        if self.changed_md5():
            ret.append("changed checksum")

        if self.is_callback or self.always_changed:
            ret.append("always changed")

        if ret:
            return {self.relpath: ret}

        return {}

    def _already_cached(self):
        return (
            not self.changed_md5()
            and all(not dep.changed() for dep in self.deps)
            and all(
                not out.changed_cache() if out.use_cache else not out.changed()
                for out in self.outs
            )
        )

    def get_all_files_number(self):
        return sum(out.get_files_number() for out in self.outs)
