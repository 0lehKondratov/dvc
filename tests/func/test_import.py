import filecmp
import os

import pytest
from mock import patch

import dvc.data_cloud as cloud
from dvc.cache import Cache
from dvc.config import NoRemoteError
from dvc.dvcfile import Dvcfile
from dvc.exceptions import DownloadError, PathMissingError
from dvc.system import System
from dvc.utils.fs import makedirs, remove
from tests.utils import trees_equal


def test_import(tmp_dir, scm, dvc, erepo_dir):
    with erepo_dir.chdir():
        erepo_dir.dvc_gen("foo", "foo content", commit="create foo")

    stage = dvc.imp(os.fspath(erepo_dir), "foo", "foo_imported")

    assert os.path.isfile("foo_imported")
    assert (tmp_dir / "foo_imported").read_text() == "foo content"
    assert scm.repo.git.check_ignore("foo_imported")
    assert stage.deps[0].def_repo == {
        "url": os.fspath(erepo_dir),
        "rev_lock": erepo_dir.scm.get_rev(),
    }


@pytest.mark.parametrize("src_is_dvc", [True, False])
def test_import_git_file(tmp_dir, scm, dvc, git_dir, src_is_dvc):
    if src_is_dvc:
        git_dir.init(dvc=True)

    git_dir.scm_gen("src", "hello", commit="add a git file")

    stage = tmp_dir.dvc.imp(os.fspath(git_dir), "src", "dst")

    assert (tmp_dir / "dst").read_text() == "hello"
    assert tmp_dir.scm.repo.git.check_ignore(os.fspath(tmp_dir / "dst"))
    assert stage.deps[0].def_repo == {
        "url": os.fspath(git_dir),
        "rev_lock": git_dir.scm.get_rev(),
    }


def test_import_cached_file(erepo_dir, tmp_dir, dvc, scm, monkeypatch):
    src = "some_file"
    dst = "some_file_imported"

    with erepo_dir.chdir():
        erepo_dir.dvc_gen({src: "hello"}, commit="add a regular file")

    tmp_dir.dvc_gen({dst: "hello"})
    (tmp_dir / dst).unlink()

    remote_exception = NoRemoteError("dvc import")
    with patch.object(cloud.DataCloud, "pull", side_effect=remote_exception):
        tmp_dir.dvc.imp(os.fspath(erepo_dir), src, dst)

    assert (tmp_dir / dst).is_file()
    assert filecmp.cmp(erepo_dir / src, tmp_dir / dst, shallow=False)


@pytest.mark.parametrize("src_is_dvc", [True, False])
def test_import_git_dir(tmp_dir, scm, dvc, git_dir, src_is_dvc):
    if src_is_dvc:
        git_dir.init(dvc=True)

    git_dir.scm_gen({"src": {"file.txt": "hello"}}, commit="add a dir")

    stage = dvc.imp(os.fspath(git_dir), "src", "dst")

    assert (tmp_dir / "dst").is_dir()
    trees_equal(git_dir / "src", tmp_dir / "dst")
    assert tmp_dir.scm.repo.git.check_ignore(os.fspath(tmp_dir / "dst"))
    assert stage.deps[0].def_repo == {
        "url": os.fspath(git_dir),
        "rev_lock": git_dir.scm.get_rev(),
    }


def test_import_dir(tmp_dir, scm, dvc, erepo_dir):
    with erepo_dir.chdir():
        erepo_dir.dvc_gen({"dir": {"foo": "foo content"}}, commit="create dir")

    stage = dvc.imp(os.fspath(erepo_dir), "dir", "dir_imported")

    assert os.path.isdir("dir_imported")
    trees_equal(erepo_dir / "dir", "dir_imported")
    assert scm.repo.git.check_ignore("dir_imported")
    assert stage.deps[0].def_repo == {
        "url": os.fspath(erepo_dir),
        "rev_lock": erepo_dir.scm.get_rev(),
    }


def test_import_file_from_dir(tmp_dir, scm, dvc, erepo_dir):
    with erepo_dir.chdir():
        erepo_dir.dvc_gen(
            {
                "dir": {
                    "1": "1",
                    "2": "2",
                    "subdir": {"foo": "foo", "bar": "bar"},
                }
            },
            commit="create dir",
        )

    stage = dvc.imp(os.fspath(erepo_dir), os.path.join("dir", "1"))

    assert (tmp_dir / "1").read_text() == "1"
    assert scm.repo.git.check_ignore("1")
    assert stage.deps[0].def_repo == {
        "url": os.fspath(erepo_dir),
        "rev_lock": erepo_dir.scm.get_rev(),
    }

    dvc.imp(os.fspath(erepo_dir), os.path.join("dir", "2"), out="file")
    assert (tmp_dir / "file").read_text() == "2"
    assert (tmp_dir / "file.dvc").exists()

    dvc.imp(os.fspath(erepo_dir), os.path.join("dir", "subdir"))
    assert (tmp_dir / "subdir" / "foo").read_text() == "foo"
    assert (tmp_dir / "subdir" / "bar").read_text() == "bar"
    assert (tmp_dir / "subdir.dvc").exists()

    dvc.imp(
        os.fspath(erepo_dir), os.path.join("dir", "subdir", "foo"), out="X"
    )
    assert (tmp_dir / "X").read_text() == "foo"
    assert (tmp_dir / "X.dvc").exists()


def test_import_non_cached(erepo_dir, tmp_dir, dvc, scm):
    src = "non_cached_output"
    dst = src + "_imported"

    with erepo_dir.chdir():
        erepo_dir.dvc.run(
            cmd=f"echo hello > {src}", outs_no_cache=[src], single_stage=True,
        )

    erepo_dir.scm_add(
        [os.fspath(erepo_dir / src)], commit="add a non-cached out"
    )

    stage = tmp_dir.dvc.imp(os.fspath(erepo_dir), src, dst)

    assert (tmp_dir / dst).is_file()
    assert filecmp.cmp(erepo_dir / src, tmp_dir / dst, shallow=False)
    assert tmp_dir.scm.repo.git.check_ignore(dst)
    assert stage.deps[0].def_repo == {
        "url": os.fspath(erepo_dir),
        "rev_lock": erepo_dir.scm.get_rev(),
    }


def test_import_rev(tmp_dir, scm, dvc, erepo_dir):
    rev = None
    with erepo_dir.chdir(), erepo_dir.branch("branch", new=True):
        erepo_dir.dvc_gen("foo", "foo content", commit="create foo on branch")
        rev = erepo_dir.scm.get_rev()

    stage = dvc.imp(os.fspath(erepo_dir), "foo", "foo_imported", rev="branch")

    assert (tmp_dir / "foo_imported").read_text() == "foo content"
    assert scm.repo.git.check_ignore("foo_imported")
    assert stage.deps[0].def_repo == {
        "url": os.fspath(erepo_dir),
        "rev": "branch",
        "rev_lock": rev,
    }


def test_pull_imported_stage(tmp_dir, dvc, erepo_dir):
    with erepo_dir.chdir():
        erepo_dir.dvc_gen("foo", "foo content", commit="create foo")
    dvc.imp(os.fspath(erepo_dir), "foo", "foo_imported")

    dst_stage = Dvcfile(dvc, "foo_imported.dvc").stage
    dst_cache = dst_stage.outs[0].cache_path

    remove("foo_imported")
    remove(dst_cache)
    dvc.pull(["foo_imported.dvc"])

    assert os.path.isfile("foo_imported")
    assert os.path.isfile(dst_cache)


def test_cache_type_is_properly_overridden(tmp_dir, scm, dvc, erepo_dir):
    with erepo_dir.chdir():
        with erepo_dir.dvc.config.edit() as conf:
            conf["cache"]["type"] = "symlink"
        erepo_dir.dvc.cache = Cache(erepo_dir.dvc)
        erepo_dir.scm_add(
            [erepo_dir.dvc.config.files["repo"]],
            "set source repo cache type to symlink",
        )
        erepo_dir.dvc_gen("foo", "foo content", "create foo")
    assert System.is_symlink(erepo_dir / "foo")

    dvc.imp(os.fspath(erepo_dir), "foo", "foo_imported")

    assert not System.is_symlink("foo_imported")
    assert (tmp_dir / "foo_imported").read_text() == "foo content"
    assert scm.repo.git.check_ignore("foo_imported")


def test_pull_imported_directory_stage(tmp_dir, dvc, erepo_dir):
    with erepo_dir.chdir():
        erepo_dir.dvc_gen({"dir": {"foo": "foo content"}}, commit="create dir")

    dvc.imp(os.fspath(erepo_dir), "dir", "dir_imported")

    remove("dir_imported")
    remove(dvc.cache.local.cache_dir)

    dvc.pull(["dir_imported.dvc"])

    assert os.path.isdir("dir_imported")
    trees_equal(erepo_dir / "dir", "dir_imported")


def test_download_error_pulling_imported_stage(tmp_dir, dvc, erepo_dir):
    with erepo_dir.chdir():
        erepo_dir.dvc_gen("foo", "foo content", commit="create foo")
    dvc.imp(os.fspath(erepo_dir), "foo", "foo_imported")

    dst_stage = Dvcfile(dvc, "foo_imported.dvc").stage
    dst_cache = dst_stage.outs[0].cache_path

    remove("foo_imported")
    remove(dst_cache)

    with patch(
        "dvc.remote.LocalRemote._download", side_effect=Exception
    ), pytest.raises(DownloadError):
        dvc.pull(["foo_imported.dvc"])


@pytest.mark.parametrize("dname", [".", "dir", "dir/subdir"])
def test_import_to_dir(dname, tmp_dir, dvc, erepo_dir):
    makedirs(dname, exist_ok=True)

    with erepo_dir.chdir():
        erepo_dir.dvc_gen("foo", "foo content", commit="create foo")

    stage = dvc.imp(os.fspath(erepo_dir), "foo", dname)

    dst = os.path.join(dname, "foo")

    assert stage.outs[0].fspath == os.path.abspath(dst)
    assert os.path.isdir(dname)
    assert (tmp_dir / dst).read_text() == "foo content"


def test_pull_non_workspace(tmp_dir, scm, dvc, erepo_dir):
    with erepo_dir.chdir():
        erepo_dir.dvc_gen("foo", "master content", commit="create foo")

        with erepo_dir.branch("branch", new=True):
            erepo_dir.dvc_gen("foo", "branch content", commit="modify foo")

    stage = dvc.imp(os.fspath(erepo_dir), "foo", "foo_imported", rev="branch")
    tmp_dir.scm_add([stage.relpath], commit="imported branch")
    scm.tag("ref-to-branch")

    # Overwrite via import
    dvc.imp(os.fspath(erepo_dir), "foo", "foo_imported", rev="master")

    remove(stage.outs[0].cache_path)
    dvc.fetch(all_tags=True)
    assert os.path.exists(stage.outs[0].cache_path)


def test_import_non_existing(erepo_dir, tmp_dir, dvc):
    with pytest.raises(PathMissingError):
        tmp_dir.dvc.imp(os.fspath(erepo_dir), "invalid_output")

    # https://github.com/iterative/dvc/pull/2837#discussion_r352123053
    with pytest.raises(PathMissingError):
        tmp_dir.dvc.imp(os.fspath(erepo_dir), "/root/", "root")


def test_pull_no_rev_lock(erepo_dir, tmp_dir, dvc):
    with erepo_dir.chdir():
        erepo_dir.dvc_gen("foo", "contents", commit="create foo")

    stage = dvc.imp(os.fspath(erepo_dir), "foo", "foo_imported")
    assert "rev" not in stage.deps[0].def_repo
    stage.deps[0].def_repo.pop("rev_lock")

    Dvcfile(dvc, stage.path).dump(stage)

    remove(stage.outs[0].cache_path)
    (tmp_dir / "foo_imported").unlink()

    dvc.pull([stage.path])

    assert (tmp_dir / "foo_imported").is_file()
    assert (tmp_dir / "foo_imported").read_text() == "contents"


def test_import_from_bare_git_repo(
    tmp_dir, make_tmp_dir, erepo_dir, setup_remote
):
    import git

    git.Repo.init(os.fspath(tmp_dir), bare=True)

    setup_remote(erepo_dir.dvc)
    with erepo_dir.chdir():
        erepo_dir.dvc_gen({"foo": "foo"}, commit="initial")
    erepo_dir.dvc.push()

    erepo_dir.scm.repo.create_remote("origin", os.fspath(tmp_dir))
    erepo_dir.scm.repo.remote("origin").push("master")

    dvc_repo = make_tmp_dir("dvc-repo", scm=True, dvc=True)
    with dvc_repo.chdir():
        dvc_repo.dvc.imp(os.fspath(tmp_dir), "foo")


def test_import_pipeline_tracked_outs(
    tmp_dir, dvc, scm, erepo_dir, run_copy, setup_remote
):
    from dvc.dvcfile import PIPELINE_FILE, PIPELINE_LOCK

    setup_remote(dvc)
    tmp_dir.gen("foo", "foo")
    run_copy("foo", "bar", name="copy-foo-bar")
    dvc.push()

    dvc.scm.add([PIPELINE_FILE, PIPELINE_LOCK])
    dvc.scm.commit("add pipeline stage")

    with erepo_dir.chdir():
        erepo_dir.dvc.imp(
            "file:///{}".format(os.fspath(tmp_dir)), "bar", out="baz"
        )
        assert (erepo_dir / "baz").read_text() == "foo"


def test_local_import(tmp_dir, dvc, scm):
    tmp_dir.dvc_gen("foo", "foo", commit="init")
    (tmp_dir / "outdir").mkdir()
    dvc.imp(".", "foo", out="outdir")
