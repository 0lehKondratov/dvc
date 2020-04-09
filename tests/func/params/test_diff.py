def test_diff_no_params(tmp_dir, scm, dvc):
    assert dvc.params.diff() == {}


def test_diff_no_changes(tmp_dir, scm, dvc):
    tmp_dir.gen("params.yaml", "foo: bar")
    dvc.run(params=["foo"])
    scm.add(["params.yaml", "Dvcfile"])
    scm.commit("bar")
    assert dvc.params.diff() == {}


def test_diff(tmp_dir, scm, dvc):
    tmp_dir.gen("params.yaml", "foo: bar")
    dvc.run(params=["foo"])
    scm.add(["params.yaml", "Dvcfile"])
    scm.commit("bar")

    tmp_dir.scm_gen("params.yaml", "foo: baz", commit="baz")
    tmp_dir.scm_gen("params.yaml", "foo: qux", commit="qux")

    assert dvc.params.diff(a_rev="HEAD~2") == {
        "params.yaml": {"foo": {"old": "bar", "new": "qux"}}
    }


def test_diff_new(tmp_dir, scm, dvc):
    tmp_dir.gen("params.yaml", "foo: bar")
    dvc.run(params=["foo"])

    assert dvc.params.diff() == {
        "params.yaml": {"foo": {"old": None, "new": "bar"}}
    }


def test_diff_deleted(tmp_dir, scm, dvc):
    tmp_dir.gen("params.yaml", "foo: bar")
    dvc.run(params=["foo"])
    scm.add(["params.yaml", "Dvcfile"])
    scm.commit("bar")

    (tmp_dir / "Dvcfile").unlink()

    assert dvc.params.diff() == {
        "params.yaml": {"foo": {"old": "bar", "new": None}}
    }


def test_diff_deleted_config(tmp_dir, scm, dvc):
    tmp_dir.gen("params.yaml", "foo: bar")
    dvc.run(params=["foo"])
    scm.add(["params.yaml", "Dvcfile"])
    scm.commit("bar")

    (tmp_dir / "params.yaml").unlink()

    assert dvc.params.diff() == {
        "params.yaml": {"foo": {"old": "bar", "new": None}}
    }


def test_diff_list(tmp_dir, scm, dvc):
    tmp_dir.gen("params.yaml", "foo:\n- bar\n- baz")
    dvc.run(params=["foo"])
    scm.add(["params.yaml", "Dvcfile"])
    scm.commit("foo")

    tmp_dir.gen("params.yaml", "foo:\n- bar\n- baz\n- qux")

    assert dvc.params.diff() == {
        "params.yaml": {
            "foo": {"old": "['bar', 'baz']", "new": "['bar', 'baz', 'qux']"}
        }
    }


def test_diff_dict(tmp_dir, scm, dvc):
    tmp_dir.gen("params.yaml", "foo:\n  bar: baz")
    dvc.run(params=["foo"])
    scm.add(["params.yaml", "Dvcfile"])
    scm.commit("foo")

    tmp_dir.gen("params.yaml", "foo:\n  bar: qux")

    assert dvc.params.diff() == {
        "params.yaml": {"foo.bar": {"old": "baz", "new": "qux"}}
    }
