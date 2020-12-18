import logging
import os
import stat

import pytest
from funcy import first

from dvc.dvcfile import PIPELINE_FILE
from dvc.repo.experiments.utils import exp_refs_by_rev
from dvc.utils.serialize import PythonFileCorruptedError
from tests.func.test_repro_multistage import COPY_SCRIPT


@pytest.mark.parametrize(
    "name,workspace",
    [(None, True), (None, False), ("foo", True), ("foo", False)],
)
def test_new_simple(tmp_dir, scm, dvc, exp_stage, mocker, name, workspace):
    baseline = scm.get_rev()
    tmp_dir.gen("params.yaml", "foo: 2")

    new_mock = mocker.spy(dvc.experiments, "new")
    results = dvc.experiments.run(
        exp_stage.addressing, name=name, tmp_dir=not workspace
    )
    exp = first(results)
    ref_info = first(exp_refs_by_rev(scm, exp))
    assert ref_info and ref_info.baseline_sha == baseline

    new_mock.assert_called_once()
    tree = scm.get_tree(exp)
    with tree.open(tmp_dir / "metrics.yaml") as fobj:
        assert fobj.read().strip() == "foo: 2"

    if workspace:
        assert (tmp_dir / "metrics.yaml").read_text().strip() == "foo: 2"

    exp_name = name if name else ref_info.name
    assert dvc.experiments.get_exact_name(exp) == exp_name
    assert scm.resolve_rev(exp_name) == exp


@pytest.mark.parametrize("workspace", [True, False])
def test_experiment_exists(tmp_dir, scm, dvc, exp_stage, mocker, workspace):
    from dvc.repo.experiments.base import ExperimentExistsError

    dvc.experiments.run(
        exp_stage.addressing,
        name="foo",
        params=["foo=2"],
        tmp_dir=not workspace,
    )

    with pytest.raises(ExperimentExistsError):
        dvc.experiments.run(
            exp_stage.addressing,
            name="foo",
            params=["foo=3"],
            tmp_dir=not workspace,
        )

    results = dvc.experiments.run(
        exp_stage.addressing,
        name="foo",
        params=["foo=3"],
        force=True,
        tmp_dir=not workspace,
    )
    exp = first(results)

    tree = scm.get_tree(exp)
    with tree.open(tmp_dir / "metrics.yaml") as fobj:
        assert fobj.read().strip() == "foo: 3"


@pytest.mark.skipif(os.name == "nt", reason="Not supported for Windows.")
def test_file_permissions(tmp_dir, scm, dvc, exp_stage, mocker):
    mode = 0o755
    os.chmod(tmp_dir / "copy.py", mode)
    scm.add(["copy.py"])
    scm.commit("set exec")

    tmp_dir.gen("params.yaml", "foo: 2")
    dvc.experiments.run(exp_stage.addressing)
    assert stat.S_IMODE(os.stat(tmp_dir / "copy.py").st_mode) == mode


def test_failed_exp(tmp_dir, scm, dvc, exp_stage, mocker, caplog):
    from dvc.exceptions import ReproductionError

    tmp_dir.gen("params.yaml", "foo: 2")

    mocker.patch(
        "concurrent.futures.Future.exception",
        return_value=ReproductionError(exp_stage.relpath),
    )
    with caplog.at_level(logging.ERROR):
        dvc.experiments.run(exp_stage.addressing, tmp_dir=True)
        assert "Failed to reproduce experiment" in caplog.text


@pytest.mark.parametrize(
    "changes, expected",
    [
        [["foo=baz"], "{foo: baz, goo: {bag: 3}, lorem: false}"],
        [["foo=baz,goo=bar"], "{foo: baz, goo: bar, lorem: false}"],
        [
            ["goo.bag=4"],
            "{foo: [bar: 1, baz: 2], goo: {bag: 4}, lorem: false}",
        ],
        [["foo[0]=bar"], "{foo: [bar, baz: 2], goo: {bag: 3}, lorem: false}"],
        [
            ["foo[1].baz=3"],
            "{foo: [bar: 1, baz: 3], goo: {bag: 3}, lorem: false}",
        ],
        [
            ["foo[1]=- baz\n- goo"],
            "{foo: [bar: 1, [baz, goo]], goo: {bag: 3}, lorem: false}",
        ],
        [
            ["lorem.ipsum=3"],
            "{foo: [bar: 1, baz: 2], goo: {bag: 3}, lorem: {ipsum: 3}}",
        ],
    ],
)
def test_modify_params(tmp_dir, scm, dvc, mocker, changes, expected):
    tmp_dir.gen("copy.py", COPY_SCRIPT)
    tmp_dir.gen(
        "params.yaml", "{foo: [bar: 1, baz: 2], goo: {bag: 3}, lorem: false}"
    )
    stage = dvc.run(
        cmd="python copy.py params.yaml metrics.yaml",
        metrics_no_cache=["metrics.yaml"],
        params=["foo", "goo", "lorem"],
        name="copy-file",
    )
    scm.add(["dvc.yaml", "dvc.lock", "copy.py", "params.yaml", "metrics.yaml"])
    scm.commit("init")

    new_mock = mocker.spy(dvc.experiments, "new")
    results = dvc.experiments.run(stage.addressing, params=changes)
    exp = first(results)

    new_mock.assert_called_once()
    tree = scm.get_tree(exp)
    with tree.open(tmp_dir / "metrics.yaml") as fobj:
        assert fobj.read().strip() == expected


@pytest.mark.parametrize("queue", [True, False])
def test_apply(tmp_dir, scm, dvc, exp_stage, queue):
    from dvc.exceptions import InvalidArgumentError
    from dvc.repo.experiments.base import ApplyConflictError

    metrics_original = (tmp_dir / "metrics.yaml").read_text().strip()
    results = dvc.experiments.run(
        exp_stage.addressing, params=["foo=2"], queue=queue, tmp_dir=True
    )
    exp_a = first(results)

    results = dvc.experiments.run(
        exp_stage.addressing, params=["foo=3"], queue=queue, tmp_dir=True
    )
    exp_b = first(results)

    with pytest.raises(InvalidArgumentError):
        dvc.experiments.apply("foo")

    dvc.experiments.apply(exp_a)
    assert (tmp_dir / "params.yaml").read_text().strip() == "foo: 2"
    assert (
        (tmp_dir / "metrics.yaml").read_text().strip() == metrics_original
        if queue
        else "foo: 2"
    )

    with pytest.raises(ApplyConflictError):
        dvc.experiments.apply(exp_b)
        # failed apply should revert everything to prior state
        assert (tmp_dir / "params.yaml").read_text().strip() == "foo: 2"
        assert (
            (tmp_dir / "metrics.yaml").read_text().strip() == metrics_original
            if queue
            else "foo: 2"
        )

    dvc.experiments.apply(exp_b, force=True)
    assert (tmp_dir / "params.yaml").read_text().strip() == "foo: 3"
    assert (
        (tmp_dir / "metrics.yaml").read_text().strip() == metrics_original
        if queue
        else "foo: 3"
    )


def test_get_baseline(tmp_dir, scm, dvc, exp_stage):
    from dvc.repo.experiments.base import EXPS_STASH

    init_rev = scm.get_rev()
    assert dvc.experiments.get_baseline(init_rev) is None

    results = dvc.experiments.run(exp_stage.addressing, params=["foo=2"])
    exp_rev = first(results)
    assert dvc.experiments.get_baseline(exp_rev) == init_rev

    dvc.experiments.run(exp_stage.addressing, params=["foo=3"], queue=True)
    assert dvc.experiments.get_baseline(f"{EXPS_STASH}@{{0}}") == init_rev

    scm.add(["dvc.yaml", "dvc.lock", "copy.py", "params.yaml", "metrics.yaml"])
    scm.commit("promote exp")
    promote_rev = scm.get_rev()
    assert dvc.experiments.get_baseline(promote_rev) is None

    results = dvc.experiments.run(exp_stage.addressing, params=["foo=4"])
    exp_rev = first(results)
    assert dvc.experiments.get_baseline(exp_rev) == promote_rev

    dvc.experiments.run(exp_stage.addressing, params=["foo=5"], queue=True)
    assert dvc.experiments.get_baseline(f"{EXPS_STASH}@{{0}}") == promote_rev
    print("stash 1")
    assert dvc.experiments.get_baseline(f"{EXPS_STASH}@{{1}}") == init_rev


def test_update_py_params(tmp_dir, scm, dvc):
    tmp_dir.gen("copy.py", COPY_SCRIPT)
    tmp_dir.gen("params.py", "INT = 1\n")
    stage = dvc.run(
        cmd="python copy.py params.py metrics.py",
        metrics_no_cache=["metrics.py"],
        params=["params.py:INT"],
        name="copy-file",
    )
    scm.add(["dvc.yaml", "dvc.lock", "copy.py", "params.py", "metrics.py"])
    scm.commit("init")

    results = dvc.experiments.run(
        stage.addressing, params=["params.py:INT=2"], tmp_dir=True
    )
    exp_a = first(results)

    tree = scm.get_tree(exp_a)
    with tree.open(tmp_dir / "params.py") as fobj:
        assert fobj.read().strip() == "INT = 2"
    with tree.open(tmp_dir / "metrics.py") as fobj:
        assert fobj.read().strip() == "INT = 2"

    tmp_dir.gen(
        "params.py",
        "INT = 1\nFLOAT = 0.001\nDICT = {'a': 1}\n\n"
        "class Train:\n    seed = 2020\n\n"
        "class Klass:\n    def __init__(self):\n        self.a = 111\n",
    )
    stage = dvc.run(
        cmd="python copy.py params.py metrics.py",
        metrics_no_cache=["metrics.py"],
        params=["params.py:INT,FLOAT,DICT,Train,Klass"],
        name="copy-file",
    )
    scm.add(["dvc.yaml", "dvc.lock", "copy.py", "params.py", "metrics.py"])
    scm.commit("init")

    results = dvc.experiments.run(
        stage.addressing,
        params=["params.py:FLOAT=0.1,Train.seed=2121,Klass.a=222"],
        tmp_dir=True,
    )
    exp_a = first(results)

    result = (
        "INT = 1\nFLOAT = 0.1\nDICT = {'a': 1}\n\n"
        "class Train:\n    seed = 2121\n\n"
        "class Klass:\n    def __init__(self):\n        self.a = 222"
    )

    def _dos2unix(text):
        if os.name != "nt":
            return text

        # NOTE: git on windows will use CRLF, so we have to convert it to LF
        # in order to compare with the original
        return text.replace("\r\n", "\n")

    tree = scm.get_tree(exp_a)
    with tree.open(tmp_dir / "params.py") as fobj:
        assert _dos2unix(fobj.read().strip()) == result
    with tree.open(tmp_dir / "metrics.py") as fobj:
        assert _dos2unix(fobj.read().strip()) == result

    tmp_dir.gen("params.py", "INT = 1\n")
    stage = dvc.run(
        cmd="python copy.py params.py metrics.py",
        metrics_no_cache=["metrics.py"],
        params=["params.py:INT"],
        name="copy-file",
    )
    scm.add(["dvc.yaml", "dvc.lock", "copy.py", "params.py", "metrics.py"])
    scm.commit("init")

    with pytest.raises(PythonFileCorruptedError):
        dvc.experiments.run(
            stage.addressing, params=["params.py:INT=2a"], tmp_dir=True
        )


def test_detached_parent(tmp_dir, scm, dvc, exp_stage, mocker):
    detached_rev = scm.get_rev()

    tmp_dir.gen("params.yaml", "foo: 2")
    dvc.reproduce(exp_stage.addressing)
    scm.add(["dvc.yaml", "dvc.lock", "copy.py", "params.yaml", "metrics.yaml"])
    scm.commit("v2")

    scm.checkout(detached_rev)
    assert scm.gitpython.repo.head.is_detached
    results = dvc.experiments.run(exp_stage.addressing, params=["foo=3"])

    exp_rev = first(results)
    assert dvc.experiments.get_baseline(exp_rev) == detached_rev
    assert (tmp_dir / "params.yaml").read_text().strip() == "foo: 3"


def test_branch(tmp_dir, scm, dvc, exp_stage):
    from dvc.exceptions import InvalidArgumentError

    with pytest.raises(InvalidArgumentError):
        dvc.experiments.branch("foo", "branch")

    scm.branch("branch-exists")

    results = dvc.experiments.run(
        exp_stage.addressing, params=["foo=2"], name="foo"
    )
    exp_a = first(results)
    ref_a = dvc.experiments.get_branch_by_rev(exp_a)

    with pytest.raises(InvalidArgumentError):
        dvc.experiments.branch("foo", "branch-exists")
    dvc.experiments.branch("foo", "branch-name")
    dvc.experiments.branch(exp_a, "branch-rev")
    dvc.experiments.branch(ref_a, "branch-ref")

    for name in ["branch-name", "branch-rev", "branch-ref"]:
        assert name in scm.list_branches()
        assert scm.resolve_rev(name) == exp_a

    tmp_dir.scm_gen({"new_file": "new_file"}, commit="new baseline")
    results = dvc.experiments.run(
        exp_stage.addressing, params=["foo=2"], name="foo"
    )
    exp_b = first(results)
    ref_b = dvc.experiments.get_branch_by_rev(exp_b)

    with pytest.raises(InvalidArgumentError):
        dvc.experiments.branch("foo", "branch-name")
    dvc.experiments.branch(ref_b, "branch-ref-b")

    assert "branch-ref-b" in scm.list_branches()
    assert scm.resolve_rev("branch-ref-b") == exp_b


def test_no_scm(tmp_dir):
    from dvc.repo import Repo as DvcRepo
    from dvc.scm.base import NoSCMError

    dvc = DvcRepo.init(no_scm=True)

    for cmd in [
        "apply",
        "branch",
        "diff",
        "show",
        "run",
        "gc",
        "push",
        "pull",
        "ls",
    ]:
        with pytest.raises(NoSCMError):
            getattr(dvc.experiments, cmd)()


def test_untracked(tmp_dir, scm, dvc, caplog):
    tmp_dir.gen("copy.py", COPY_SCRIPT)
    tmp_dir.gen("params.yaml", "foo: 1")
    stage = dvc.run(
        cmd="python copy.py params.yaml metrics.yaml",
        metrics_no_cache=["metrics.yaml"],
        params=["foo"],
        name="copy-file",
    )
    scm.add(["dvc.yaml", "dvc.lock", "params.yaml", "metrics.yaml"])
    scm.commit("init")

    # copy.py is untracked
    with caplog.at_level(logging.ERROR):
        results = dvc.experiments.run(
            stage.addressing, params=["foo=2"], tmp_dir=True
        )
        assert "Failed to reproduce experiment" in caplog.text
        assert not results

    # copy.py is staged as new file but not committed
    scm.add(["copy.py"])
    results = dvc.experiments.run(
        stage.addressing, params=["foo=2"], tmp_dir=True
    )
    exp = first(results)
    tree = scm.get_tree(exp)
    assert tree.exists("copy.py")
    with tree.open(tmp_dir / "metrics.yaml") as fobj:
        assert fobj.read().strip() == "foo: 2"


@pytest.mark.parametrize("workspace", [True, False])
def test_dirty_lockfile(tmp_dir, scm, dvc, exp_stage, workspace):
    from dvc.dvcfile import LockfileCorruptedError

    tmp_dir.gen("dvc.lock", "foo")

    with pytest.raises(LockfileCorruptedError):
        dvc.reproduce(exp_stage.addressing)

    results = dvc.experiments.run(
        exp_stage.addressing, params=["foo=2"], tmp_dir=not workspace
    )
    exp = first(results)

    tree = scm.get_tree(exp)
    with tree.open(tmp_dir / "metrics.yaml") as fobj:
        assert fobj.read().strip() == "foo: 2"

    if not workspace:
        assert (tmp_dir / "dvc.lock").read_text() == "foo"


def test_packed_args_exists(tmp_dir, scm, dvc, exp_stage, caplog):
    from dvc.repo.experiments.executor.base import BaseExecutor

    tmp_dir.scm_gen(
        tmp_dir / ".dvc" / "tmp" / BaseExecutor.PACKED_ARGS_FILE,
        "",
        commit="commit args file",
    )

    with caplog.at_level(logging.WARNING):
        dvc.experiments.run(exp_stage.addressing)
        assert "Temporary DVC file" in caplog.text


def test_list(tmp_dir, scm, dvc, exp_stage):
    baseline_a = scm.get_rev()
    results = dvc.experiments.run(exp_stage.addressing, params=["foo=2"])
    exp_a = first(results)
    ref_info_a = first(exp_refs_by_rev(scm, exp_a))

    results = dvc.experiments.run(exp_stage.addressing, params=["foo=3"])
    exp_b = first(results)
    ref_info_b = first(exp_refs_by_rev(scm, exp_b))

    tmp_dir.scm_gen("new", "new", commit="new")
    baseline_c = scm.get_rev()
    results = dvc.experiments.run(exp_stage.addressing, params=["foo=4"])
    exp_c = first(results)
    ref_info_c = first(exp_refs_by_rev(scm, exp_c))

    assert dvc.experiments.ls() == {
        baseline_c: [ref_info_c.name],
    }

    exp_list = dvc.experiments.ls(rev=ref_info_a.baseline_sha)
    assert {key: set(val) for key, val in exp_list.items()} == {
        baseline_a: {ref_info_a.name, ref_info_b.name}
    }

    exp_list = dvc.experiments.ls(all_=True)
    assert {key: set(val) for key, val in exp_list.items()} == {
        baseline_a: {ref_info_a.name, ref_info_b.name},
        baseline_c: {ref_info_c.name},
    }


@pytest.mark.parametrize("workspace", [True, False])
def test_subdir(tmp_dir, scm, dvc, workspace):
    subdir = tmp_dir / "dir"
    subdir.gen("copy.py", COPY_SCRIPT)
    subdir.gen("params.yaml", "foo: 1")

    with subdir.chdir():
        dvc.run(
            cmd="python copy.py params.yaml metrics.yaml",
            metrics_no_cache=["metrics.yaml"],
            params=["foo"],
            name="copy-file",
            no_exec=True,
        )
        scm.add(
            [subdir / "dvc.yaml", subdir / "copy.py", subdir / "params.yaml"]
        )
        scm.commit("init")

        results = dvc.experiments.run(
            PIPELINE_FILE, params=["foo=2"], tmp_dir=not workspace
        )
        assert results

    exp = first(results)
    ref_info = first(exp_refs_by_rev(scm, exp))

    tree = scm.get_tree(exp)
    for fname in ["metrics.yaml", "dvc.lock"]:
        assert tree.exists(subdir / fname)
    with tree.open(subdir / "metrics.yaml") as fobj:
        assert fobj.read().strip() == "foo: 2"

    assert dvc.experiments.get_exact_name(exp) == ref_info.name
    assert scm.resolve_rev(ref_info.name) == exp


@pytest.mark.parametrize("workspace", [True, False])
def test_subrepo(tmp_dir, scm, workspace):
    from tests.unit.tree.test_repo import make_subrepo

    subrepo = tmp_dir / "dir" / "repo"
    make_subrepo(subrepo, scm)

    subrepo.gen("copy.py", COPY_SCRIPT)
    subrepo.gen("params.yaml", "foo: 1")

    with subrepo.chdir():
        subrepo.dvc.run(
            cmd="python copy.py params.yaml metrics.yaml",
            metrics_no_cache=["metrics.yaml"],
            params=["foo"],
            name="copy-file",
            no_exec=True,
        )
        scm.add(
            [
                subrepo / "dvc.yaml",
                subrepo / "copy.py",
                subrepo / "params.yaml",
            ]
        )
        scm.commit("init")

        results = subrepo.dvc.experiments.run(
            PIPELINE_FILE, params=["foo=2"], tmp_dir=not workspace
        )
        assert results

    exp = first(results)
    ref_info = first(exp_refs_by_rev(scm, exp))

    tree = scm.get_tree(exp)
    for fname in ["metrics.yaml", "dvc.lock"]:
        assert tree.exists(subrepo / fname)
    with tree.open(subrepo / "metrics.yaml") as fobj:
        assert fobj.read().strip() == "foo: 2"

    assert subrepo.dvc.experiments.get_exact_name(exp) == ref_info.name
    assert scm.resolve_rev(ref_info.name) == exp


def test_queue(tmp_dir, scm, dvc, exp_stage, mocker):
    dvc.experiments.run(exp_stage.addressing, params=["foo=2"], queue=True)
    dvc.experiments.run(exp_stage.addressing, params=["foo=3"], queue=True)
    assert len(dvc.experiments.stash_revs) == 2

    repro_mock = mocker.spy(dvc.experiments, "_reproduce_revs")
    results = dvc.experiments.run(run_all=True)
    assert len(results) == 2
    repro_mock.assert_called_with(jobs=1)

    expected = {"foo: 2", "foo: 3"}
    metrics = set()
    for exp in results:
        tree = scm.get_tree(exp)
        with tree.open(tmp_dir / "metrics.yaml") as fobj:
            metrics.add(fobj.read().strip())
    assert expected == metrics
