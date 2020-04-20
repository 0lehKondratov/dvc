import pytest

from dvc.dvcfile import Dvcfile
from dvc.stage import Stage
from dvc.loader import StageNotFound
from dvc.stage.exceptions import StageFileDoesNotExistError


def test_run_load_one_for_multistage(tmp_dir, dvc):
    tmp_dir.gen("foo", "foo")
    stage1 = dvc.run(
        cmd="cp foo foo2",
        deps=["foo"],
        name="copy-foo-foo2",
        outs_persist_no_cache=["foo2"],
        always_changed=True,
    )
    stage2 = Dvcfile(dvc, "Dvcfile").stages["copy-foo-foo2"]
    assert stage1 == stage2
    foo_out = stage2.outs[0]
    assert stage2.cmd == "cp foo foo2"
    assert stage2.name == "copy-foo-foo2"
    assert foo_out.def_path == "foo2"
    assert foo_out.persist
    assert not foo_out.use_cache
    assert stage2.deps[0].def_path == "foo"
    assert dvc.reproduce(":copy-foo-foo2")


def test_run_load_one_for_multistage_non_existing(tmp_dir, dvc):
    with pytest.raises(StageFileDoesNotExistError):
        assert Dvcfile(dvc, "Dvcfile").stages.get("copy-foo-foo2")


def test_run_load_one_for_multistage_non_existing_stage_name(tmp_dir, dvc):
    tmp_dir.gen("foo", "foo")
    stage = dvc.run(
        cmd="cp foo foo2",
        deps=["foo"],
        name="copy-foo-foo2",
        metrics=["foo2"],
        always_changed=True,
    )
    with pytest.raises(StageNotFound):
        assert Dvcfile(dvc, stage.path).stages["random-name"]


def test_run_load_one_on_single_stage(tmp_dir, dvc):
    tmp_dir.gen("foo", "foo")
    stage = dvc.run(
        cmd="cp foo foo2", deps=["foo"], metrics=["foo2"], always_changed=True,
    )
    assert Dvcfile(dvc, stage.path).stages.get("random-name")
    assert Dvcfile(dvc, stage.path).stage


def test_has_stage_with_name(tmp_dir, dvc):
    tmp_dir.gen("foo", "foo")
    dvc.run(
        cmd="cp foo foo2",
        deps=["foo"],
        name="copy-foo-foo2",
        metrics=["foo2"],
        always_changed=True,
    )
    dvcfile = Dvcfile(dvc, "Dvcfile")
    assert "copy-foo-foo2" in dvcfile.stages
    assert "copy" not in dvcfile.stages


def test_load_all_multistage(tmp_dir, dvc):
    tmp_dir.gen("foo", "foo")
    stage1 = dvc.run(
        cmd="cp foo foo2",
        deps=["foo"],
        name="copy-foo-foo2",
        metrics=["foo2"],
        always_changed=True,
    )
    stages = Dvcfile(dvc, "Dvcfile").stages.values()
    assert len(stages) == 1
    assert list(stages) == [stage1]

    tmp_dir.gen("bar", "bar")
    stage2 = dvc.run(
        cmd="cp bar bar2",
        deps=["bar"],
        name="copy-bar-bar2",
        metrics=["bar2"],
        always_changed=True,
    )
    assert set(Dvcfile(dvc, "Dvcfile").stages.values()) == {stage2, stage1}


def test_load_all_singlestage(tmp_dir, dvc):
    tmp_dir.gen("foo", "foo")
    stage1 = dvc.run(
        cmd="cp foo foo2", deps=["foo"], metrics=["foo2"], always_changed=True,
    )
    stages = Dvcfile(dvc, "foo2.dvc").stages.values()
    assert len(stages) == 1
    assert list(stages) == [stage1]


def test_load_singlestage(tmp_dir, dvc):
    tmp_dir.gen("foo", "foo")
    stage1 = dvc.run(
        cmd="cp foo foo2", deps=["foo"], metrics=["foo2"], always_changed=True,
    )
    assert Dvcfile(dvc, "foo2.dvc").stage == stage1


def test_load_multistage(tmp_dir, dvc):
    from dvc.dvcfile import MultiStageFileLoadError

    tmp_dir.gen("foo", "foo")
    dvc.run(
        cmd="cp foo foo2",
        deps=["foo"],
        name="copy-foo-foo2",
        metrics=["foo2"],
        always_changed=True,
    )
    with pytest.raises(MultiStageFileLoadError):
        Dvcfile(dvc, "Dvcfile").stage


def test_is_multistage(tmp_dir, dvc):
    tmp_dir.gen({"foo": "foo", "bar": "bar"})
    stage1 = dvc.run(
        cmd="cp foo foo2",
        deps=["foo"],
        name="copy-foo-foo2",
        metrics=["foo2"],
        always_changed=True,
    )
    assert Dvcfile(dvc, stage1.path).is_multi_stage()
    stage2 = dvc.run(
        cmd="cp bar bar2", deps=["bar"], metrics=["bar2"], always_changed=True,
    )
    assert not Dvcfile(dvc, stage2.path).is_multi_stage()


def test_stage_collection(tmp_dir, dvc):
    tmp_dir.gen(
        {
            "dir": {"file1": "file1", "file2": "file2"},
            "foo": "foo",
            "bar": "bar",
        }
    )
    (stage1,) = dvc.add("dir")
    stage2 = dvc.run(
        cmd="cp foo foo2",
        deps=["foo"],
        name="copy-foo-foo2",
        metrics=["foo2"],
        always_changed=True,
    )
    stage3 = dvc.run(
        cmd="cp bar bar2", deps=["bar"], metrics=["bar2"], always_changed=True,
    )
    assert {s for s in dvc.pipeline_stages} == {stage3, stage2, stage1}
    assert {s for s in dvc.stages} == {Stage(dvc, "foo2.dvc"), stage1, stage3}
