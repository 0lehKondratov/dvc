import pytest

from dvc.repo.params.show import NoParamsError


def test_show_empty(dvc):
    with pytest.raises(NoParamsError):
        dvc.params.show()


def test_show(tmp_dir, dvc):
    tmp_dir.gen("params.yaml", "foo: bar")
    dvc.run(cmd="echo params.yaml", params=["foo"], single_stage=True)
    assert dvc.params.show() == {"": {"params.yaml": {"foo": "bar"}}}


def test_show_multiple(tmp_dir, dvc):
    tmp_dir.gen("params.yaml", "foo: bar\nbaz: qux\n")
    dvc.run(
        cmd="echo params.yaml",
        fname="foo.dvc",
        params=["foo"],
        single_stage=True,
    )
    dvc.run(
        cmd="echo params.yaml",
        fname="baz.dvc",
        params=["baz"],
        single_stage=True,
    )
    assert dvc.params.show() == {
        "": {"params.yaml": {"foo": "bar", "baz": "qux"}}
    }


def test_show_list(tmp_dir, dvc):
    tmp_dir.gen("params.yaml", "foo:\n- bar\n- baz\n")
    dvc.run(cmd="echo params.yaml", params=["foo"], single_stage=True)
    assert dvc.params.show() == {"": {"params.yaml": {"foo": ["bar", "baz"]}}}


def test_show_branch(tmp_dir, scm, dvc):
    tmp_dir.gen("params.yaml", "foo: bar")
    dvc.run(cmd="echo params.yaml", params=["foo"], single_stage=True)
    scm.add(["params.yaml", "Dvcfile"])
    scm.commit("init")

    with tmp_dir.branch("branch", new=True):
        tmp_dir.scm_gen("params.yaml", "foo: baz", commit="branch")

    assert dvc.params.show(revs=["branch"]) == {
        "workspace": {"params.yaml": {"foo": "bar"}},
        "branch": {"params.yaml": {"foo": "baz"}},
    }


def test_pipeline_tracked_params(tmp_dir, scm, dvc, run_copy):
    from dvc.dvcfile import PIPELINE_FILE

    tmp_dir.gen({"foo": "foo", "params.yaml": "foo: bar\nxyz: val"})
    run_copy("foo", "bar", name="copy-foo-bar", params=["foo,xyz"])
    scm.add(["params.yaml", PIPELINE_FILE])
    scm.commit("add stage")

    tmp_dir.scm_gen("params.yaml", "foo: baz\nxyz: val", commit="baz")
    tmp_dir.scm_gen("params.yaml", "foo: qux\nxyz: val", commit="qux")

    assert dvc.params.show(revs=["master"]) == {
        "master": {"params.yaml": {"foo": "qux", "xyz": "val"}}
    }
