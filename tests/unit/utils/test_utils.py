import re
import os

import pytest

from dvc.path_info import PathInfo
from dvc.utils import (
    file_md5,
    resolve_output,
    fix_env,
    relpath,
    to_chunks,
    tmp_fname,
    parse_target,
)


@pytest.mark.parametrize(
    "chunk_size, expected_chunks",
    [(1, [[1], [2], [3], [4]]), (2, [[1, 2], [3, 4]]), (3, [[1, 2, 3], [4]])],
)
def test_to_chunks_chunk_size(chunk_size, expected_chunks):
    list_to_chunk = [1, 2, 3, 4]
    result = list(to_chunks(list_to_chunk, chunk_size=chunk_size))
    assert result == expected_chunks


@pytest.mark.parametrize("num_chunks, chunk_size", [(1, 2), (None, None)])
def test_to_chunks_should_raise(num_chunks, chunk_size):
    list_to_chunk = [1, 2, 3]
    with pytest.raises(ValueError):
        to_chunks(list_to_chunk, num_chunks, chunk_size)


@pytest.mark.parametrize(
    "num_chunks, expected_chunks",
    [(4, [[1], [2], [3], [4]]), (3, [[1, 2], [3, 4]]), (2, [[1, 2], [3, 4]])],
)
def test_to_chunks_num_chunks(num_chunks, expected_chunks):
    list_to_chunk = [1, 2, 3, 4]
    result = to_chunks(list_to_chunk, num_chunks=num_chunks)
    assert result == expected_chunks


@pytest.mark.skipif(os.name == "nt", reason="pyenv-win is not supported")
@pytest.mark.parametrize(
    "path, orig",
    [
        (
            (
                "/pyenv/bin:/pyenv/libexec:/pyenv/plugins/plugin:"
                "/orig/path1:/orig/path2"
            ),
            "/orig/path1:/orig/path2",
        ),
        (
            "/pyenv/bin:/pyenv/libexec:/orig/path1:/orig/path2",
            "/orig/path1:/orig/path2",
        ),
        (
            (
                "/pyenv/bin:/some/libexec:/pyenv/plugins/plugin:"
                "/orig/path1:/orig/path2"
            ),
            "/orig/path1:/orig/path2",
        ),
        ("/orig/path1:/orig/path2", "/orig/path1:/orig/path2"),
        (
            "/orig/path1:/orig/path2:/pyenv/bin:/pyenv/libexec",
            "/orig/path1:/orig/path2:/pyenv/bin:/pyenv/libexec",
        ),
    ],
)
def test_fix_env_pyenv(path, orig):
    env = {
        "PATH": path,
        "PYENV_ROOT": "/pyenv",
        "PYENV_VERSION": "3.7.2",
        "PYENV_DIR": "/some/dir",
        "PYENV_HOOK_PATH": "/some/hook/path",
    }
    assert fix_env(env)["PATH"] == orig


def test_file_md5(tmp_dir):
    tmp_dir.gen("foo", "foo content")

    assert file_md5("foo") == file_md5(PathInfo("foo"))


def test_tmp_fname():
    file_path = os.path.join("path", "to", "file")
    file_path_info = PathInfo(file_path)

    def pattern(path):
        return r"^" + re.escape(path) + r"\.[a-z0-9]{22}\.tmp$"

    assert re.search(pattern(file_path), tmp_fname(file_path), re.IGNORECASE)
    assert re.search(
        pattern(file_path_info.fspath),
        tmp_fname(file_path_info),
        re.IGNORECASE,
    )


def test_relpath():
    path = "path"
    path_info = PathInfo(path)

    assert relpath(path) == relpath(path_info)


@pytest.mark.parametrize(
    "inp,out,is_dir,expected",
    [
        ["target", None, False, "target"],
        ["target", "dir", True, os.path.join("dir", "target")],
        ["target", "file_target", False, "file_target"],
        [
            "target",
            os.path.join("dir", "subdir"),
            True,
            os.path.join("dir", "subdir", "target"),
        ],
        ["dir/", None, False, "dir"],
        ["dir", None, False, "dir"],
        ["dir", "other_dir", False, "other_dir"],
        ["dir", "other_dir", True, os.path.join("other_dir", "dir")],
    ],
)
def test_resolve_output(inp, out, is_dir, expected, mocker):
    mocker.patch("os.path.isdir", return_value=is_dir)
    result = resolve_output(inp, out)
    assert result == expected


@pytest.mark.parametrize(
    "inp,out, default",
    [
        ["pipelines.yaml", ("pipelines.yaml", None, None), None],
        ["pipelines.yaml:name", ("pipelines.yaml", "name", None), None],
        [":name", ("pipelines.yaml", "name", None), None],
        ["stage.dvc", ("stage.dvc", None, None), None],
        ["pipelines.yaml:name@v1", ("pipelines.yaml", "name", "v1"), None],
        ["../models/stage.dvc", ("../models/stage.dvc", None, None), "def"],
        [":name", ("default", "name", None), "default"],
        [":name@v2", ("default", "name", "v2"), "default"],
    ],
)
def test_parse_target(inp, out, default):
    assert parse_target(inp, default) == out


def test_hint_on_lockfile():
    with pytest.raises(Exception) as exc:
        assert parse_target("pipelines.lock:name@v223")
    assert "pipelines.yaml:name@v223" in str(exc.value)
