import pytest

from dvc.config import ConfigError
from dvc.fs import get_cloud_fs
from dvc.path_info import CloudURLInfo


def test_get_cloud_fs(tmp_dir, dvc):
    tmp_dir.add_remote(name="base", url="s3://bucket/path", default=False)
    tmp_dir.add_remote(name="first", url="remote://base/first", default=False)
    tmp_dir.add_remote(
        name="second", url="remote://first/second", default=False
    )

    base = CloudURLInfo("s3://bucket/path")
    first = base / "first"
    second = first / "second"

    cls, config = get_cloud_fs(dvc, name="base")
    assert cls(**config).path_info == base
    cls, config = get_cloud_fs(dvc, name="first")
    assert cls(**config).path_info == first
    cls, config = get_cloud_fs(dvc, name="second")
    assert cls(**config).path_info == second


def test_get_cloud_fs_validate(tmp_dir, dvc):
    tmp_dir.add_remote(
        name="base", url="ssh://example.com/path", default=False
    )
    tmp_dir.add_remote(
        name="first",
        config={"url": "remote://base/first", "type": "symlink"},
        default=False,
    )
    tmp_dir.add_remote(
        name="second",
        config={"url": "remote://first/second", "oss_key_id": "mykey"},
        default=False,
    )

    assert get_cloud_fs(dvc, name="base")[1] == {
        "url": "ssh://example.com/path"
    }
    assert get_cloud_fs(dvc, name="first")[1] == {
        "url": "ssh://example.com/path/first",
        "type": ["symlink"],
    }

    with pytest.raises(ConfigError):
        get_cloud_fs(dvc, name="second")
