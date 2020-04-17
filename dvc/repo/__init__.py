import os
from contextlib import contextmanager
from functools import wraps

from dvc.ignore import CleanTree
from dvc.compat import fspath_py35

from funcy import cached_property, cat, first

from dvc.config import Config
from dvc.exceptions import (
    FileMissingError,
    IsADirectoryError,
    NotDvcRepoError,
    OutputNotFoundError,
)
from dvc.path_info import PathInfo
from dvc.remote.base import RemoteActionNotImplemented
from dvc.utils.fs import path_isin
from .graph import check_acyclic, get_pipeline, get_pipelines


def locked(f):
    @wraps(f)
    def wrapper(repo, *args, **kwargs):
        with repo.lock, repo.state:
            repo._reset()
            ret = f(repo, *args, **kwargs)
            # Our graph cache is no longer valid after we release the repo.lock
            repo._reset()
            return ret

    return wrapper


class Repo(object):
    DVC_DIR = ".dvc"

    from dvc.repo.destroy import destroy
    from dvc.repo.install import install
    from dvc.repo.add import add
    from dvc.repo.remove import remove
    from dvc.repo.ls import ls
    from dvc.repo.lock import lock as lock_stage
    from dvc.repo.move import move
    from dvc.repo.run import run
    from dvc.repo.imp import imp
    from dvc.repo.imp_url import imp_url
    from dvc.repo.reproduce import reproduce
    from dvc.repo.checkout import _checkout
    from dvc.repo.push import push
    from dvc.repo.fetch import _fetch
    from dvc.repo.pull import pull
    from dvc.repo.status import status
    from dvc.repo.gc import gc
    from dvc.repo.commit import commit
    from dvc.repo.diff import diff
    from dvc.repo.brancher import brancher
    from dvc.repo.get import get
    from dvc.repo.get_url import get_url
    from dvc.repo.update import update

    def __init__(self, root_dir=None):
        from dvc.state import State
        from dvc.lock import make_lock
        from dvc.scm import SCM
        from dvc.cache import Cache
        from dvc.data_cloud import DataCloud
        from dvc.repo.metrics import Metrics
        from dvc.repo.params import Params
        from dvc.scm.tree import WorkingTree
        from dvc.repo.tag import Tag
        from dvc.utils.fs import makedirs

        root_dir = self.find_root(root_dir)

        self.root_dir = os.path.abspath(os.path.realpath(root_dir))
        self.dvc_dir = os.path.join(self.root_dir, self.DVC_DIR)

        self.config = Config(self.dvc_dir)

        no_scm = self.config["core"].get("no_scm", False)
        self.scm = SCM(self.root_dir, no_scm=no_scm)

        self.tree = WorkingTree(self.root_dir)

        self.tmp_dir = os.path.join(self.dvc_dir, "tmp")
        makedirs(self.tmp_dir, exist_ok=True)

        hardlink_lock = self.config["core"].get("hardlink_lock", False)
        self.lock = make_lock(
            os.path.join(self.dvc_dir, "lock"),
            tmp_dir=os.path.join(self.dvc_dir, "tmp"),
            hardlink_lock=hardlink_lock,
            friendly=True,
        )

        # NOTE: storing state and link_state in the repository itself to avoid
        # any possible state corruption in 'shared cache dir' scenario.
        self.state = State(self)

        self.cache = Cache(self)
        self.cloud = DataCloud(self)

        self.metrics = Metrics(self)
        self.params = Params(self)
        self.tag = Tag(self)

        self._ignore()

    @property
    def tree(self):
        return self._tree

    @tree.setter
    def tree(self, tree):
        self._tree = tree if isinstance(tree, CleanTree) else CleanTree(tree)
        # Our graph cache is no longer valid, as it was based on the previous
        # tree.
        self._reset()

    def __repr__(self):
        return "{}: '{}'".format(self.__class__.__name__, self.root_dir)

    @classmethod
    def find_root(cls, root=None):
        root_dir = os.path.realpath(root or os.curdir)

        if not os.path.isdir(root_dir):
            raise NotDvcRepoError("directory '{}' does not exist".format(root))

        while True:
            dvc_dir = os.path.join(root_dir, cls.DVC_DIR)
            if os.path.isdir(dvc_dir):
                return root_dir
            if os.path.ismount(root_dir):
                break
            root_dir = os.path.dirname(root_dir)

        message = (
            "you are not inside of a DVC repository "
            "(checked up to mount point '{}')"
        ).format(root_dir)
        raise NotDvcRepoError(message)

    @classmethod
    def find_dvc_dir(cls, root=None):
        root_dir = cls.find_root(root)
        return os.path.join(root_dir, cls.DVC_DIR)

    @staticmethod
    def init(root_dir=os.curdir, no_scm=False, force=False, subdir=False):
        from dvc.repo.init import init

        init(root_dir=root_dir, no_scm=no_scm, force=force, subdir=subdir)
        return Repo(root_dir)

    def unprotect(self, target):
        return self.cache.local.unprotect(PathInfo(target))

    def _ignore(self):
        from dvc.updater import Updater

        updater = Updater(self.dvc_dir)

        flist = (
            [self.config.files["local"], updater.updater_file]
            + [self.lock.lockfile, updater.lock.lockfile, self.tmp_dir]
            + self.state.files
        )

        if path_isin(self.cache.local.cache_dir, self.root_dir):
            flist += [self.cache.local.cache_dir]

        self.scm.ignore_list(flist)

    def check_modified_graph(self, new_stages, old_stages=None):
        """Generate graph including the new stage to check for errors"""
        # Building graph might be costly for the ones with many DVC-files,
        # so we provide this undocumented hack to skip it. See [1] for
        # more details. The hack can be used as:
        #
        #     repo = Repo(...)
        #     repo._skip_graph_checks = True
        #     repo.add(...)
        #
        # A user should care about not duplicating outs and not adding cycles,
        # otherwise DVC might have an undefined behaviour.
        #
        # [1] https://github.com/iterative/dvc/issues/2671
        if not getattr(self, "_skip_graph_checks", False):
            self._collect_graph((old_stages or self.stages) + new_stages)

    def _collect_inside(self, path, graph):
        import networkx as nx

        stages = nx.dfs_postorder_nodes(graph or self.pipeline_graph)
        return [stage for stage in stages if path_isin(stage.path, path)]

    def collect_for_pipelines(
        self, path=None, name=None, recursive=False, graph=None
    ):
        from ..dvcfile import Dvcfile

        if not path:
            return list(graph) if graph else self.pipeline_stages

        path = os.path.abspath(path)
        if recursive and os.path.isdir(path):
            return self._collect_inside(path, graph or self.pipeline_graph)

        dvcfile = Dvcfile(self, path)
        dvcfile.check_file_exists()
        return [dvcfile.load_one(name)]

    def collect(self, target, with_deps=False, recursive=False, graph=None):
        import networkx as nx
        from ..dvcfile import Dvcfile

        if not target:
            return list(graph) if graph else self.stages

        target = os.path.abspath(target)

        if recursive and os.path.isdir(target):
            return self._collect_inside(target, graph or self.graph)

        stage = Dvcfile(self, target).load()

        # Optimization: do not collect the graph for a specific target
        if not with_deps:
            return [stage]

        pipeline = get_pipeline(get_pipelines(graph or self.graph), stage)
        return list(nx.dfs_postorder_nodes(pipeline, stage))

    def collect_granular(self, target, *args, **kwargs):
        from ..dvcfile import Dvcfile

        if not target:
            return [(stage, None) for stage in self.stages]

        # Optimization: do not collect the graph for a specific .dvc target
        if Dvcfile.is_valid_filename(target) and not kwargs.get("with_deps"):
            return [(Dvcfile(self, target).load(), None)]

        try:
            (out,) = self.find_outs_by_path(target, strict=False)
            filter_info = PathInfo(os.path.abspath(target))
            return [(out.stage, filter_info)]
        except OutputNotFoundError:
            stages = self.collect(target, *args, **kwargs)
            return [(stage, None) for stage in stages]

    def used_cache(
        self,
        targets=None,
        all_branches=False,
        with_deps=False,
        all_tags=False,
        all_commits=False,
        remote=None,
        force=False,
        jobs=None,
        recursive=False,
    ):
        """Get the stages related to the given target and collect
        the `info` of its outputs.

        This is useful to know what files from the cache are _in use_
        (namely, a file described as an output on a stage).

        The scope is, by default, the working directory, but you can use
        `all_branches`/`all_tags`/`all_commits` to expand the scope.

        Returns:
            A dictionary with Schemes (representing output's location) mapped
            to items containing the output's `dumpd` names and the output's
            children (if the given output is a directory).
        """
        from dvc.cache import NamedCache

        cache = NamedCache()

        for branch in self.brancher(
            all_branches=all_branches,
            all_tags=all_tags,
            all_commits=all_commits,
        ):
            targets = targets or [None]

            pairs = cat(
                self.collect_granular(
                    target, recursive=recursive, with_deps=with_deps
                )
                for target in targets
            )

            suffix = "({})".format(branch) if branch else ""
            for stage, filter_info in pairs:
                used_cache = stage.get_used_cache(
                    remote=remote,
                    force=force,
                    jobs=jobs,
                    filter_info=filter_info,
                )
                cache.update(used_cache, suffix=suffix)

        return cache

    def _collect_graph(self, stages=None):
        """Generate a graph by using the given stages on the given directory

        The nodes of the graph are the stage's path relative to the root.

        Edges are created when the output of one stage is used as a
        dependency in other stage.

        The direction of the edges goes from the stage to its dependency:

        For example, running the following:

            $ dvc run -o A "echo A > A"
            $ dvc run -d A -o B "echo B > B"
            $ dvc run -d B -o C "echo C > C"

        Will create the following graph:

               ancestors <--
                           |
                C.dvc -> B.dvc -> A.dvc
                |          |
                |          --> descendants
                |
                ------- pipeline ------>
                           |
                           v
              (weakly connected components)

        Args:
            stages (list): used to build a graph, if None given, collect stages
                in the repository.

        Raises:
            OutputDuplicationError: two outputs with the same path
            StagePathAsOutputError: stage inside an output directory
            OverlappingOutputPathsError: output inside output directory
            CyclicGraphError: resulting graph has cycles
        """
        import networkx as nx
        from pygtrie import Trie
        from dvc.exceptions import (
            OutputDuplicationError,
            StagePathAsOutputError,
            OverlappingOutputPathsError,
        )

        G = nx.DiGraph()
        stages = stages or self.stages
        stages = [stage for stage in stages if stage]
        outs = Trie()  # Use trie to efficiently find overlapping outs and deps

        for stage in stages:
            for out in stage.outs:
                out_key = out.path_info.parts

                # Check for dup outs
                if out_key in outs:
                    dup_stages = [stage, outs[out_key].stage]
                    raise OutputDuplicationError(str(out), dup_stages)

                # Check for overlapping outs
                if outs.has_subtrie(out_key):
                    parent = out
                    overlapping = first(outs.values(prefix=out_key))
                else:
                    parent = outs.shortest_prefix(out_key).value
                    overlapping = out
                if parent and overlapping:
                    msg = (
                        "Paths for outs:\n'{}'('{}')\n'{}'('{}')\n"
                        "overlap. To avoid unpredictable behaviour, "
                        "rerun command with non overlapping outs paths."
                    ).format(
                        str(parent),
                        parent.stage.relpath,
                        str(overlapping),
                        overlapping.stage.relpath,
                    )
                    raise OverlappingOutputPathsError(parent, overlapping, msg)

                outs[out_key] = out

        for stage in stages:
            out = outs.shortest_prefix(PathInfo(stage.path).parts).value
            if out:
                raise StagePathAsOutputError(stage, str(out))

        # Building graph
        G.add_nodes_from(stages)
        for stage in stages:
            for dep in stage.deps:
                if dep.path_info is None:
                    continue

                dep_key = dep.path_info.parts
                overlapping = list(n.value for n in outs.prefixes(dep_key))
                if outs.has_subtrie(dep_key):
                    overlapping.extend(outs.values(prefix=dep_key))

                G.add_edges_from((stage, out.stage) for out in overlapping)
        check_acyclic(G)

        return G

    @cached_property
    def graph(self):
        return self._collect_graph(self.stages)

    @cached_property
    def pipelines(self):
        return get_pipelines(self.pipeline_graph)

    @cached_property
    def stages(self):
        """
        Walks down the root directory looking for Dvcfiles,
        skipping the directories that are related with
        any SCM (e.g. `.git`), DVC itself (`.dvc`), or directories
        tracked by DVC (e.g. `dvc add data` would skip `data/`)

        NOTE: For large repos, this could be an expensive
              operation. Consider using some memoization.
        """
        return self._collect_stages()[0]

    @cached_property
    def pipeline_stages(self):
        return self._collect_stages()[1]

    @cached_property
    def pipeline_graph(self):
        return self._collect_graph(self.pipeline_stages)

    def _collect_stages(self):
        from dvc.dvcfile import Dvcfile
        from dvc.stage import PipelineStage

        pipeline_stages = []
        single_stages = []
        output_stages = []
        ignored_outs = []
        outs = set()

        for root, dirs, files in self.tree.walk(self.root_dir):
            for fname in files:
                path = os.path.join(root, fname)
                if not Dvcfile.is_valid_filename(path):
                    continue
                stgs = Dvcfile(self, path).load_all()
                ignored_outs.extend(
                    out
                    for stage in stgs
                    if isinstance(stage, PipelineStage)
                    for out in stage.outs
                )

                for stage in stgs:
                    stages = (
                        output_stages
                        if stage.is_data_source
                        else pipeline_stages
                    )
                    stages.append(stage)
                    if not (
                        isinstance(stage, PipelineStage)
                        or stage.is_data_source
                    ):
                        single_stages.append(stage)
                    for out in stage.outs:
                        if out.scheme == "local":
                            outs.add(out.fspath)

            dirs[:] = [d for d in dirs if os.path.join(root, d) not in outs]

        # DVC files are generated by multi-stage for data management.
        # We need to ignore those stages for pipelines_stages, but still
        # should be collected for output stages.
        _output_stages = [
            stage
            for stage in output_stages
            if all(
                stage.outs and out.fspath != stage.outs[0].fspath
                for out in ignored_outs
            )
        ]
        # Old single-stages are used for both outputs and pipelines
        # so they go into both buckets: pipeline_stages and output stages.
        return (
            output_stages + single_stages,
            pipeline_stages + _output_stages,
        )

    def find_outs_by_path(self, path, outs=None, recursive=False, strict=True):
        if not outs:
            outs = [out for stage in self.stages for out in stage.outs]

        abs_path = os.path.abspath(path)
        path_info = PathInfo(abs_path)
        match = path_info.__eq__ if strict else path_info.isin_or_eq

        def func(out):
            if out.scheme == "local" and match(out.path_info):
                return True

            if recursive and out.path_info.isin(path_info):
                return True

            return False

        matched = list(filter(func, outs))
        if not matched:
            raise OutputNotFoundError(path, self)

        return matched

    def find_out_by_relpath(self, relpath):
        path = os.path.join(self.root_dir, relpath)
        (out,) = self.find_outs_by_path(path)
        return out

    def is_dvc_internal(self, path):
        path_parts = os.path.normpath(path).split(os.path.sep)
        return self.DVC_DIR in path_parts

    @contextmanager
    def open_by_relpath(self, path, remote=None, mode="r", encoding=None):
        """Opens a specified resource as a file descriptor"""
        cause = None
        try:
            out = self.find_out_by_relpath(path)
        except OutputNotFoundError as exc:
            out = None
            cause = exc

        if out and out.use_cache:
            try:
                with self._open_cached(out, remote, mode, encoding) as fd:
                    yield fd
                return
            except FileNotFoundError as exc:
                raise FileMissingError(path) from exc

        abs_path = os.path.join(self.root_dir, path)
        if os.path.exists(abs_path):
            with open(abs_path, mode=mode, encoding=encoding) as fd:
                yield fd
            return

        raise FileMissingError(path) from cause

    def _open_cached(self, out, remote=None, mode="r", encoding=None):
        if out.isdir():
            raise IsADirectoryError("Can't open a dir")

        cache_file = self.cache.local.checksum_to_path_info(out.checksum)
        cache_file = fspath_py35(cache_file)

        if os.path.exists(cache_file):
            return open(cache_file, mode=mode, encoding=encoding)

        try:
            remote_obj = self.cloud.get_remote(remote)
            remote_info = remote_obj.checksum_to_path_info(out.checksum)
            return remote_obj.open(remote_info, mode=mode, encoding=encoding)
        except RemoteActionNotImplemented:
            with self.state:
                cache_info = out.get_used_cache(remote=remote)
                self.cloud.pull(cache_info, remote=remote)

            return open(cache_file, mode=mode, encoding=encoding)

    def close(self):
        self.scm.close()

    @locked
    def checkout(self, *args, **kwargs):
        return self._checkout(*args, **kwargs)

    @locked
    def fetch(self, *args, **kwargs):
        return self._fetch(*args, **kwargs)

    def _reset(self):
        self.__dict__.pop("graph", None)
        self.__dict__.pop("stages", None)
        self.__dict__.pop("pipelines", None)
        self.__dict__.pop("dvcignore", None)
        self.__dict__.pop("pipeline_graph", None)
        self.__dict__.pop("pipeline_stages", None)
