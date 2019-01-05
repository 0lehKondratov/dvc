import collections
import os
import dvc.prompt as prompt
import dvc.logger as logger

from dvc.exceptions import DvcException, MoveNotDataSourceError
from dvc.exceptions import NotDvcProjectError


class InitError(DvcException):
    def __init__(self, msg):
        super(InitError, self).__init__(msg)


class ReproductionError(DvcException):
    def __init__(self, dvc_file_name, ex):
        self.path = dvc_file_name
        msg = "failed to reproduce '{}'".format(dvc_file_name)
        super(ReproductionError, self).__init__(msg, cause=ex)


class Project(object):
    DVC_DIR = '.dvc'

    def __init__(self, root_dir=None):
        from dvc.config import Config
        from dvc.state import State
        from dvc.lock import Lock
        from dvc.scm import SCM
        from dvc.cache import Cache
        from dvc.data_cloud import DataCloud
        from dvc.updater import Updater

        root_dir = self._find_root(root_dir)

        self.root_dir = os.path.abspath(os.path.realpath(root_dir))
        self.dvc_dir = os.path.join(self.root_dir, self.DVC_DIR)

        self.config = Config(self.dvc_dir)

        self.scm = SCM(self.root_dir, project=self)
        self.lock = Lock(self.dvc_dir)
        # NOTE: storing state and link_state in the repository itself to avoid
        # any possible state corruption in 'shared cache dir' scenario.
        self.state = State(self, self.config._config)

        core = self.config._config[Config.SECTION_CORE]

        logger.set_level(core.get(Config.SECTION_CORE_LOGLEVEL))

        self.cache = Cache(self)
        self.cloud = DataCloud(self, config=self.config._config)
        self.updater = Updater(self.dvc_dir)

        self._files_to_git_add = []

        self._ignore()

        self.updater.check()

    def __repr__(self):
        return "Project: '{root_dir}'".format(root_dir=self.root_dir)

    @staticmethod
    def _find_root(root=None):
        if root is None:
            root = os.getcwd()
        else:
            root = os.path.abspath(os.path.realpath(root))

        while True:
            dvc_dir = os.path.join(root, Project.DVC_DIR)
            if os.path.isdir(dvc_dir):
                return root
            if os.path.ismount(root):
                break
            root = os.path.dirname(root)
        raise NotDvcProjectError(root)

    @staticmethod
    def _find_dvc_dir(root=None):
        root_dir = Project._find_root(root)
        return os.path.join(root_dir, Project.DVC_DIR)

    def _remind_to_git_add(self):
        if len(self._files_to_git_add) == 0:
            return

        msg = '\nTo track the changes with git run:\n\n'
        msg += '\tgit add ' + " ".join(self._files_to_git_add)

        logger.info(msg)

    @staticmethod
    def init(root_dir=os.curdir, no_scm=False, force=False):
        """
        Initiate dvc project in directory.

        Args:
            root_dir: Path to project's root directory.

        Returns:
            Project instance.

        Raises:
            KeyError: Raises an exception.
        """
        import shutil
        from dvc.scm import SCM, Base
        from dvc.config import Config

        root_dir = os.path.abspath(root_dir)
        dvc_dir = os.path.join(root_dir, Project.DVC_DIR)
        scm = SCM(root_dir)
        if type(scm) == Base and not no_scm:
            msg = "{} is not tracked by any supported scm tool(e.g. git)."
            raise InitError(msg.format(root_dir))

        if os.path.isdir(dvc_dir):
            if not force:
                msg = "'{}' exists. Use '-f' to force."
                raise InitError(msg.format(os.path.relpath(dvc_dir)))
            shutil.rmtree(dvc_dir)

        os.mkdir(dvc_dir)

        config = Config.init(dvc_dir)
        proj = Project(root_dir)

        scm.add([config.config_file])

        if scm.ignore_file():
            scm.add([os.path.join(dvc_dir, scm.ignore_file())])
            logger.info('\nYou can now commit the changes to git.\n')

        proj._welcome_message()

        return proj

    @staticmethod
    def load_all(projects_paths):
        """
        Instantiate all projects in the given list of paths.

        Args:
            projects_paths: List of paths to projects.

        Returns:
            List of Project instances in the same order of the given paths.
        """
        return [Project(path) for path in projects_paths]

    def destroy(self):
        import shutil

        for stage in self.stages():
            stage.remove()

        shutil.rmtree(self.dvc_dir)

    def _ignore(self):
        flist = [
            self.state.state_file,
            self.lock.lock_file,
            self.config.config_local_file,
            self.updater.updater_file,
            self.updater.lock.lock_file,
        ] + self.state.temp_files

        if self.cache.local.cache_dir.startswith(self.root_dir):
            flist += [self.cache.local.cache_dir]

        self.scm.ignore_list(flist)

    def install(self):
        self.scm.install()

    def to_dvc_path(self, path):
        return os.path.relpath(path, self.root_dir)

    def _check_cwd_specified_as_output(self, cwd, stages):
        from dvc.exceptions import WorkingDirectoryAsOutputError

        cwd_path = os.path.abspath(os.path.normpath(cwd))

        for stage in stages:
            for output in stage.outs:
                if os.path.isdir(output.path) and output.path == cwd_path:
                    raise WorkingDirectoryAsOutputError(cwd, stage.relpath)

    def _check_output_duplication(self, outs, stages):
        from dvc.exceptions import OutputDuplicationError

        for stage in stages:
            for o in stage.outs:
                for out in outs:
                    if o.path == out.path and o.stage.path != out.stage.path:
                        stages = [o.stage.relpath, out.stage.relpath]
                        raise OutputDuplicationError(o.path, stages)

    def add(self, fname, recursive=False):
        from dvc.stage import Stage

        fnames = []
        if recursive and os.path.isdir(fname):
            fnames = []
            for root, dirs, files in os.walk(fname):
                for f in files:
                    path = os.path.join(root, f)
                    if Stage.is_stage_file(path):
                        continue
                    if os.path.basename(path) == self.scm.ignore_file():
                        continue
                    if self.scm.is_tracked(path):
                        continue
                    fnames.append(path)
        else:
            fnames = [fname]

        all_stages = self.stages()
        stages = []
        self._files_to_git_add = []
        with self.state:
            for f in fnames:
                stage = Stage.create(project=self,
                                     outs=[f],
                                     add=True)

                if stage is None:
                    stages.append(stage)
                    continue

                self._check_output_duplication(stage.outs, all_stages)

                stage.save()
                stage.dump()
                stages.append(stage)

        self._remind_to_git_add()

        return stages

    def remove(self, target, outs_only=False):
        from dvc.stage import Stage

        stage = Stage.load(self, target)
        if outs_only:
            stage.remove_outs()
        else:
            stage.remove()

        return stage

    def lock_stage(self, target, unlock=False):
        from dvc.stage import Stage

        stage = Stage.load(self, target)
        stage.locked = False if unlock else True
        stage.dump()

        return stage

    def move(self, from_path, to_path):
        import dvc.output as Output
        from dvc.stage import Stage

        from_out = Output.loads_from(Stage(self, cwd=os.curdir),
                                     [from_path])[0]

        found = False
        self._files_to_git_add = []
        with self.state:
            for stage in self.stages():
                for out in stage.outs:
                    if out.path != from_out.path:
                        continue

                    if not stage.is_data_source:
                        raise MoveNotDataSourceError(stage.relpath)

                    found = True
                    to_out = Output.loads_from(out.stage,
                                               [to_path],
                                               out.cache,
                                               out.metric)[0]
                    out.move(to_out)

                    stage_base = os.path.basename(stage.path)
                    stage_base = stage_base.rstrip(Stage.STAGE_FILE_SUFFIX)

                    stage_dir = os.path.dirname(stage.path)
                    from_base = os.path.basename(from_path)
                    to_base = os.path.basename(to_path)
                    if stage_base == from_base:
                        os.unlink(stage.path)
                        path = to_base + Stage.STAGE_FILE_SUFFIX
                        stage.path = os.path.join(stage_dir, path)

                stage.dump()

        self._remind_to_git_add()

        if not found:
            msg = "unable to find dvcfile with output '{}'"
            raise DvcException(msg.format(from_path))

    def _unprotect_file(self, path):
        import stat
        import uuid
        from dvc.system import System
        from dvc.utils import copyfile, move, remove

        if System.is_symlink(path) or System.is_hardlink(path):
            logger.debug("Unprotecting '{}'".format(path))

            tmp = os.path.join(os.path.dirname(path), '.' + str(uuid.uuid4()))
            move(path, tmp)

            copyfile(tmp, path)

            remove(tmp)
        else:
            logger.debug("Skipping copying for '{}', since it is not "
                         "a symlink or a hardlink.".format(path))

        os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)

    def _unprotect_dir(self, path):
        for root, dirs, files in os.walk(path):
            for f in files:
                path = os.path.join(root, f)
                self._unprotect_file(path)

    def unprotect(self, path):
        if not os.path.exists(path):
            raise DvcException(
                "can't unprotect non-existing data '{}'"
                .format(path)
            )

        if os.path.isdir(path):
            self._unprotect_dir(path)
        else:
            self._unprotect_file(path)

    def run(self,
            cmd=None,
            deps=[],
            outs=[],
            outs_no_cache=[],
            metrics_no_cache=[],
            fname=None,
            cwd=os.curdir,
            no_exec=False,
            overwrite=False,
            ignore_build_cache=False,
            remove_outs=False):
        from dvc.stage import Stage

        with self.state:
            stage = Stage.create(project=self,
                                 fname=fname,
                                 cmd=cmd,
                                 cwd=cwd,
                                 outs=outs,
                                 outs_no_cache=outs_no_cache,
                                 metrics_no_cache=metrics_no_cache,
                                 deps=deps,
                                 overwrite=overwrite,
                                 ignore_build_cache=ignore_build_cache,
                                 remove_outs=remove_outs)

        if stage is None:
            return None

        all_stages = self.stages()

        self._check_cwd_specified_as_output(cwd, all_stages)
        self._check_output_duplication(stage.outs, all_stages)

        self._files_to_git_add = []
        with self.state:
            if not no_exec:
                stage.run()

        stage.dump()

        self._remind_to_git_add()

        return stage

    def imp(self, url, out):
        from dvc.stage import Stage

        stage = Stage.create(project=self,
                             cmd=None,
                             deps=[url],
                             outs=[out])

        if stage is None:
            return None

        self._check_output_duplication(stage.outs, self.stages())

        self._files_to_git_add = []
        with self.state:
            stage.run()

        stage.dump()

        self._remind_to_git_add()

        return stage

    def _reproduce_stage(self, stages, node, force, dry, interactive):
        stage = stages[node]

        if stage.locked:
            logger.warning(
                "DVC file '{path}' is locked. Its dependencies are"
                " not going to be reproduced."
                .format(path=stage.relpath)
            )

        stage = stage.reproduce(force=force, dry=dry, interactive=interactive)
        if not stage:
            return []

        if not dry:
            stage.dump()

        return [stage]

    def reproduce(self,
                  target=None,
                  recursive=True,
                  force=False,
                  dry=False,
                  interactive=False,
                  pipeline=False,
                  all_pipelines=False,
                  ignore_build_cache=False):
        from dvc.stage import Stage

        if target is None and not all_pipelines:
            raise ValueError()

        if not interactive:
            config = self.config
            core = config._config[config.SECTION_CORE]
            interactive = core.get(config.SECTION_CORE_INTERACTIVE, False)

        targets = []
        if pipeline or all_pipelines:
            if pipeline:
                stage = Stage.load(self, target)
                node = os.path.relpath(stage.path, self.root_dir)
                pipelines = [self._get_pipeline(node)]
            else:
                pipelines = self.pipelines()

            for G in pipelines:
                for node in G.nodes():
                    if G.in_degree(node) == 0:
                        targets.append(os.path.join(self.root_dir, node))
        else:
            targets.append(target)

        self._files_to_git_add = []

        ret = []
        with self.state:
            for target in targets:
                stages = self._reproduce(target,
                                         recursive=recursive,
                                         force=force,
                                         dry=dry,
                                         interactive=interactive,
                                         ignore_build_cache=ignore_build_cache)
                ret.extend(stages)

        self._remind_to_git_add()

        return ret

    def _reproduce(self,
                   target,
                   recursive=True,
                   force=False,
                   dry=False,
                   interactive=False,
                   ignore_build_cache=False):
        import networkx as nx
        from dvc.stage import Stage

        stage = Stage.load(self, target)
        G = self.graph()[1]
        stages = nx.get_node_attributes(G, 'stage')
        node = os.path.relpath(stage.path, self.root_dir)

        if recursive:
            ret = self._reproduce_stages(G,
                                         stages,
                                         node,
                                         force,
                                         dry,
                                         interactive,
                                         ignore_build_cache)
        else:
            ret = self._reproduce_stage(stages,
                                        node,
                                        force,
                                        dry,
                                        interactive)

        return ret

    def _reproduce_stages(self,
                          G,
                          stages,
                          node,
                          force,
                          dry,
                          interactive,
                          ignore_build_cache):
        import networkx as nx

        result = []
        for n in nx.dfs_postorder_nodes(G, node):
            try:
                ret = self._reproduce_stage(stages,
                                            n,
                                            force,
                                            dry,
                                            interactive)

                if len(ret) == 0 and ignore_build_cache:
                    # NOTE: we are walking our pipeline from the top to the
                    # bottom. If one stage is changed, it will be reproduced,
                    # which tells us that we should force reproducing all of
                    # the other stages down below, even if their direct
                    # dependencies didn't change.
                    force = True

                result += ret
            except Exception as ex:
                raise ReproductionError(stages[n].relpath, ex)
        return result

    def _cleanup_unused_links(self, all_stages):
        used = []
        for stage in all_stages:
            for out in stage.outs:
                used.append(out.path)
        self.state.remove_unused_links(used)

    def checkout(self, target=None, with_deps=False, force=False):
        all_stages = self.active_stages()
        stages = all_stages

        if target:
            stages = self._collect(target, with_deps=with_deps)

        with self.state:
            self._cleanup_unused_links(all_stages)

            for stage in stages:
                if stage.locked:
                    logger.warning(
                        "DVC file '{path}' is locked. Its dependencies are"
                        " not going to be checked out."
                        .format(path=stage.relpath)
                    )

                stage.checkout(force=force)

    def _get_pipeline(self, node):
        pipelines = list(filter(lambda g: node in g.nodes(),
                                self.pipelines()))
        assert len(pipelines) == 1
        return pipelines[0]

    def _collect(self, target, with_deps=False):
        import networkx as nx
        from dvc.stage import Stage

        stage = Stage.load(self, target)
        if not with_deps:
            return [stage]

        node = os.path.relpath(stage.path, self.root_dir)
        G = self._get_pipeline(node)
        stages = nx.get_node_attributes(G, 'stage')

        ret = [stage]
        for n in nx.dfs_postorder_nodes(G, node):
            ret.append(stages[n])

        return ret

    def _collect_dir_cache(self,
                           out,
                           branch=None,
                           remote=None,
                           force=False,
                           jobs=None):
        info = out.dumpd()
        ret = [info]
        r = out.remote
        md5 = info[r.PARAM_CHECKSUM]

        if self.cache.local.changed_cache_file(md5):
            try:
                self.cloud.pull(ret,
                                jobs=jobs,
                                remote=remote,
                                show_checksums=False)
            except DvcException as exc:
                msg = "Failed to pull cache for '{}': {}"
                logger.debug(msg.format(out, exc))

        if self.cache.local.changed_cache_file(md5):
            msg = "Missing cache for directory '{}'. " \
                  "Cache for files inside will be lost. " \
                  "Would you like to continue? Use '-f' to force. "
            if not force and not prompt.confirm(msg):
                raise DvcException(
                    "unable to fully collect used cache"
                    " without cache for directory '{}'"
                    .format(out)
                )
            else:
                return ret

        for i in self.cache.local.load_dir_cache(md5):
            i['branch'] = branch
            i[r.PARAM_PATH] = os.path.join(info[r.PARAM_PATH],
                                           i[r.PARAM_RELPATH])
            ret.append(i)

        return ret

    def _collect_used_cache(self,
                            out,
                            branch=None,
                            remote=None,
                            force=False,
                            jobs=None):
        if not out.use_cache or not out.info:
            if not out.info:
                logger.warning("Output '{}'({}) is missing version "
                               "info. Cache for it will not be collected. "
                               "Use dvc repro to get your pipeline up to "
                               "date.".format(out, out.stage))
            return []

        info = out.dumpd()
        info['branch'] = branch
        ret = [info]

        if out.scheme != 'local':
            return ret

        md5 = info[out.remote.PARAM_CHECKSUM]
        cache = self.cache.local.get(md5)
        if not out.remote.is_dir_cache(cache):
            return ret

        return self._collect_dir_cache(out,
                                       branch=branch,
                                       remote=remote,
                                       force=force,
                                       jobs=jobs)

    def _used_cache(self,
                    target=None,
                    all_branches=False,
                    active=True,
                    with_deps=False,
                    all_tags=False,
                    remote=None,
                    force=False,
                    jobs=None):
        cache = {}
        cache['local'] = []
        cache['s3'] = []
        cache['gs'] = []
        cache['hdfs'] = []
        cache['ssh'] = []
        cache['azure'] = []

        for branch in self.scm.brancher(all_branches=all_branches,
                                        all_tags=all_tags):
            if target:
                stages = self._collect(target,
                                       with_deps=with_deps)
            elif active:
                stages = self.active_stages()
            else:
                stages = self.stages()

            for stage in stages:
                if active and not target and stage.locked:
                    logger.warning(
                        "DVC file '{path}' is locked. Its dependencies are"
                        " not going to be pushed/pulled/fetched."
                        .format(path=stage.relpath)
                    )

                for out in stage.outs:
                    scheme = out.path_info['scheme']
                    cache[scheme] += self._collect_used_cache(out,
                                                              branch=branch,
                                                              remote=remote,
                                                              force=force,
                                                              jobs=jobs)

        return cache

    @staticmethod
    def merge_cache_lists(clists):
        merged_cache = collections.defaultdict(list)

        for cache_list in clists:
            for scheme, cache in cache_list.items():
                for item in cache:
                    if item not in merged_cache[scheme]:
                        merged_cache[scheme].append(item)

        return merged_cache

    @staticmethod
    def load_all_used_cache(projects,
                            target=None,
                            all_branches=False,
                            active=True,
                            with_deps=False,
                            all_tags=False,
                            remote=None,
                            force=False,
                            jobs=None):
        clists = []

        for project in projects:
            with project.state:
                project_clist = project._used_cache(target=None,
                                                    all_branches=all_branches,
                                                    active=False,
                                                    with_deps=with_deps,
                                                    all_tags=all_tags,
                                                    remote=remote,
                                                    force=force,
                                                    jobs=jobs)

                clists.append(project_clist)

        return clists

    def _do_gc(self, typ, func, clist):
        removed = func(clist)
        if not removed:
            logger.info("No unused {} cache to remove.".format(typ))

    def gc(self,
           all_branches=False,
           cloud=False,
           remote=None,
           with_deps=False,
           all_tags=False,
           force=False,
           jobs=None,
           projects=None):

        all_projects = [self]

        if projects is not None and len(projects) > 0:
            all_projects.extend(Project.load_all(projects))

        all_clists = Project.load_all_used_cache(all_projects,
                                                 target=None,
                                                 all_branches=all_branches,
                                                 active=False,
                                                 with_deps=with_deps,
                                                 all_tags=all_tags,
                                                 remote=remote,
                                                 force=force,
                                                 jobs=jobs)

        if len(all_clists) > 1:
            clist = Project.merge_cache_lists(all_clists)
        else:
            clist = all_clists[0]

        with self.state:
            self._do_gc('local', self.cache.local.gc, clist)

            if self.cache.s3:
                self._do_gc('s3', self.cache.s3.gc, clist)

            if self.cache.gs:
                self._do_gc('gs', self.cache.gs.gc, clist)

            if self.cache.ssh:
                self._do_gc('ssh', self.cache.ssh.gc, clist)

            if self.cache.hdfs:
                self._do_gc('hdfs', self.cache.hdfs.gc, clist)

            if self.cache.azure:
                self._do_gc('azure', self.cache.azure.gc, clist)

            if cloud:
                self._do_gc('remote', self.cloud._get_cloud(remote,
                                                            'gc -c').gc, clist)

    def push(self,
             target=None,
             jobs=1,
             remote=None,
             all_branches=False,
             show_checksums=False,
             with_deps=False,
             all_tags=False):
        with self.state:
            used = self._used_cache(target,
                                    all_branches=all_branches,
                                    all_tags=all_tags,
                                    with_deps=with_deps,
                                    force=True,
                                    remote=remote,
                                    jobs=jobs)['local']
            self.cloud.push(used,
                            jobs,
                            remote=remote,
                            show_checksums=show_checksums)

    def fetch(self,
              target=None,
              jobs=1,
              remote=None,
              all_branches=False,
              show_checksums=False,
              with_deps=False,
              all_tags=False):
        with self.state:
            used = self._used_cache(target,
                                    all_branches=all_branches,
                                    all_tags=all_tags,
                                    with_deps=with_deps,
                                    force=True,
                                    remote=remote,
                                    jobs=jobs)['local']
            self.cloud.pull(used,
                            jobs,
                            remote=remote,
                            show_checksums=show_checksums)

    def pull(self,
             target=None,
             jobs=1,
             remote=None,
             all_branches=False,
             show_checksums=False,
             with_deps=False,
             all_tags=False,
             force=False):
        self.fetch(target,
                   jobs,
                   remote=remote,
                   all_branches=all_branches,
                   all_tags=all_tags,
                   show_checksums=show_checksums,
                   with_deps=with_deps)
        self.checkout(target=target, with_deps=with_deps, force=force)

    def _local_status(self, target=None, with_deps=False):
        status = {}

        if target:
            stages = self._collect(target,
                                   with_deps=with_deps)
        else:
            stages = self.active_stages()

        for stage in stages:
            if stage.locked:
                logger.warning(
                    "DVC file '{path}' is locked. Its dependencies are"
                    " not going to be shown in the status output."
                    .format(path=stage.relpath)
                )

            status.update(stage.status())

        return status

    def _cloud_status(self,
                      target=None,
                      jobs=1,
                      remote=None,
                      show_checksums=False,
                      all_branches=False,
                      with_deps=False,
                      all_tags=False):
        import dvc.remote.base as cloud

        used = self._used_cache(target,
                                all_branches=all_branches,
                                all_tags=all_tags,
                                with_deps=with_deps,
                                force=True,
                                remote=remote,
                                jobs=jobs)['local']

        ret = {}
        status_info = self.cloud.status(used,
                                        jobs,
                                        remote=remote,
                                        show_checksums=show_checksums)
        for md5, info in status_info.items():
            name = info['name']
            status = info['status']
            if status == cloud.STATUS_OK:
                continue

            prefix_map = {
                cloud.STATUS_DELETED: 'deleted',
                cloud.STATUS_NEW: 'new',
            }

            ret[name] = prefix_map[status]

        return ret

    def status(self,
               target=None,
               jobs=1,
               cloud=False,
               remote=None,
               show_checksums=False,
               all_branches=False,
               with_deps=False,
               all_tags=False):
        with self.state:
            if cloud:
                return self._cloud_status(target,
                                          jobs,
                                          remote=remote,
                                          show_checksums=show_checksums,
                                          all_branches=all_branches,
                                          with_deps=with_deps,
                                          all_tags=all_tags)
            return self._local_status(target,
                                      with_deps=with_deps)

    def _read_metric_json(self, fd, json_path):
        import json
        from jsonpath_rw import parse

        parser = parse(json_path)
        return [x.value for x in parser.find(json.load(fd))]

    def _do_read_metric_xsv(self, reader, row, col):
        if col is not None and row is not None:
            return [reader[row][col]]
        elif col is not None:
            return [r[col] for r in reader]
        elif row is not None:
            return reader[row]
        return None

    def _read_metric_hxsv(self, fd, hxsv_path, delimiter):
        import csv

        col, row = hxsv_path.split(',')
        row = int(row)
        reader = list(csv.DictReader(fd, delimiter=delimiter))
        return self._do_read_metric_xsv(reader, row, col)

    def _read_metric_xsv(self, fd, xsv_path, delimiter):
        import csv

        col, row = xsv_path.split(',')
        row = int(row)
        col = int(col)
        reader = list(csv.reader(fd, delimiter=delimiter))
        return self._do_read_metric_xsv(reader, row, col)

    def _read_metric(self, path, typ=None, xpath=None):
        ret = None

        if not os.path.exists(path):
            return ret

        try:
            with open(path, 'r') as fd:
                if typ == 'json':
                    ret = self._read_metric_json(fd, xpath)
                elif typ == 'csv':
                    ret = self._read_metric_xsv(fd, xpath, ',')
                elif typ == 'tsv':
                    ret = self._read_metric_xsv(fd, xpath, '\t')
                elif typ == 'hcsv':
                    ret = self._read_metric_hxsv(fd, xpath, ',')
                elif typ == 'htsv':
                    ret = self._read_metric_hxsv(fd, xpath, '\t')
                else:
                    ret = fd.read()
        except Exception:
            logger.error("unable to read metric in '{}'".format(path))

        return ret

    def _find_output_by_path(self, path, outs=None):
        from dvc.exceptions import OutputDuplicationError

        if not outs:
            astages = self.active_stages()
            outs = [out for stage in astages for out in stage.outs]

        abs_path = os.path.abspath(path)
        matched = [out for out in outs if out.path == abs_path]
        stages = [out.stage.relpath for out in matched]
        if len(stages) > 1:
            raise OutputDuplicationError(path, stages)

        return matched[0] if matched else None

    def metrics_show(self,
                     path=None,
                     typ=None,
                     xpath=None,
                     all_branches=False,
                     all_tags=False):
        res = {}
        for branch in self.scm.brancher(all_branches=all_branches,
                                        all_tags=all_tags):
            astages = self.active_stages()
            outs = [out for stage in astages for out in stage.outs]

            if path:
                out = self._find_output_by_path(path, outs=outs)
                stage = out.stage.path if out else None
                if out and all([out.metric,
                                not typ,
                                isinstance(out.metric, dict)]):
                    entries = [(path,
                                out.metric.get(out.PARAM_METRIC_TYPE, None),
                                out.metric.get(out.PARAM_METRIC_XPATH, None))]
                else:
                    entries = [(path, typ, xpath)]
            else:
                metrics = filter(lambda o: o.metric, outs)
                stage = None
                entries = []
                for o in metrics:
                    if not typ and isinstance(o.metric, dict):
                        t = o.metric.get(o.PARAM_METRIC_TYPE, typ)
                        x = o.metric.get(o.PARAM_METRIC_XPATH, xpath)
                    else:
                        t = typ
                        x = xpath
                    entries.append((o.path, t, x))

            for fname, t, x in entries:
                if stage:
                    self.checkout(stage, force=True)

                rel = os.path.relpath(fname)
                metric = self._read_metric(fname,
                                           typ=t,
                                           xpath=x)
                if not metric:
                    continue

                if branch not in res:
                    res[branch] = {}

                res[branch][rel] = metric

        for branch, val in res.items():
            if all_branches or all_tags:
                logger.info('{}:'.format(branch))
            for fname, metric in val.items():
                logger.info('\t{}: {}'.format(fname, metric))

        if res:
            return res

        if path:
            msg = "file '{}' does not exist".format(path)
        else:
            msg = (
                "no metric files in this repository."
                " use 'dvc metrics add' to add a metric file to track."
            )

        raise DvcException(msg)

    def _metrics_modify(self, path, typ=None, xpath=None, delete=False):
        out = self._find_output_by_path(path)
        if not out:
            msg = "unable to find file '{}' in the pipeline".format(path)
            raise DvcException(msg)

        if out.scheme != 'local':
            msg = "output '{}' scheme '{}' is not supported for metrics"
            raise DvcException(msg.format(out.path, out.path_info['scheme']))

        if out.use_cache:
            msg = "cached output '{}' is not supported for metrics"
            raise DvcException(msg.format(out.rel_path))

        if typ:
            if not isinstance(out.metric, dict):
                out.metric = {}
            out.metric[out.PARAM_METRIC_TYPE] = typ

        if xpath:
            if not isinstance(out.metric, dict):
                out.metric = {}
            out.metric[out.PARAM_METRIC_XPATH] = xpath

        if delete:
            out.metric = None

        out._verify_metric()

        out.stage.dump()

    def metrics_modify(self, path=None, typ=None, xpath=None):
        self._metrics_modify(path, typ, xpath)

    def metrics_add(self, path, typ=None, xpath=None):
        if not typ:
            typ = 'raw'
        self._metrics_modify(path, typ, xpath)

    def metrics_remove(self, path):
        self._metrics_modify(path, delete=True)

    def graph(self):
        import networkx as nx
        from dvc.exceptions import OutputDuplicationError

        G = nx.DiGraph()
        G_active = nx.DiGraph()
        stages = self.stages()

        outs = []
        outs_by_path = {}
        for stage in stages:
            for o in stage.outs:
                existing = outs_by_path.get(o.path, None)
                if existing is not None:
                    stages = [o.stage.relpath, existing.stage.relpath]
                    raise OutputDuplicationError(o.path, stages)
                outs.append(o)
                outs_by_path[o.path] = o

        # collect the whole DAG
        for stage in stages:
            node = os.path.relpath(stage.path, self.root_dir)

            G.add_node(node, stage=stage)
            G_active.add_node(node, stage=stage)

            for dep in stage.deps:
                for out in outs:
                    if out.path != dep.path \
                       and not dep.path.startswith(out.path + out.sep) \
                       and not out.path.startswith(dep.path + dep.sep):
                        continue

                    dep_stage = out.stage
                    dep_node = os.path.relpath(dep_stage.path, self.root_dir)
                    G.add_node(dep_node, stage=dep_stage)
                    G.add_edge(node, dep_node)
                    if not stage.locked:
                        G_active.add_node(dep_node, stage=dep_stage)
                        G_active.add_edge(node, dep_node)

        return G, G_active

    def pipelines(self):
        import networkx as nx

        G, G_active = self.graph()

        return [
            G.subgraph(c).copy()
            for c in nx.weakly_connected_components(G)
        ]

    def stages(self):
        """
        Walks down the root directory looking for Dvcfiles,
        skipping the directories that are related with
        any SCM (e.g. `.git`) or DVC itself (`.dvc`).

        NOTE: For large projects, this could be an expensive
              operation.  Consider using some memorization.
        """
        from dvc.stage import Stage

        stages = []
        outs = []
        for root, dirs, files in os.walk(self.root_dir):
            for fname in files:
                path = os.path.join(root, fname)
                if not Stage.is_stage_file(path):
                    continue
                stage = Stage.load(self, path)
                for out in stage.outs:
                    outs.append(out.path + out.sep)
                stages.append(stage)

            def filter_dirs(dname):
                path = os.path.join(root, dname)
                if path == self.dvc_dir or path == self.scm.dir:
                    return False
                for out in outs:
                    if path == os.path.normpath(out) or path.startswith(out):
                        return False
                return True

            dirs[:] = list(filter(filter_dirs, dirs))

        return stages

    def active_stages(self):
        import networkx as nx

        stages = []
        for G in self.pipelines():
            stages.extend(list(nx.get_node_attributes(G, 'stage').values()))
        return stages

    def _welcome_message(self):
        import colorama

        logger.box(
            "DVC has enabled anonymous aggregate usage analytics.\n"
            "Read the analytics documentation (and how to opt-out) here:\n"
            "{blue}https://dvc.org/doc/user-guide/analytics{nc}"
            .format(
                blue=colorama.Fore.BLUE,
                nc=colorama.Fore.RESET
            ),
            border_color='red'
        )

        logger.info(
            "{yellow}What's next?{nc}\n"
            "{yellow}------------{nc}\n"
            "- Check out the documentation: {blue}https://dvc.org/doc{nc}\n"
            "- Get help and share ideas: {blue}https://dvc.org/chat{nc}\n"
            "- Star us on GitHub: {blue}https://github.com/iterative/dvc{nc}"
            .format(yellow=colorama.Fore.YELLOW,
                    blue=colorama.Fore.BLUE,
                    nc=colorama.Fore.RESET)
        )
