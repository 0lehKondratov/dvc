import pytest

from dvc.config import ConfigError
from dvc.remote.s3 import S3Remote

bucket_name = "bucket-name"
prefix = "some/prefix"
url = f"s3://{bucket_name}/{prefix}"


@pytest.fixture(autouse=True)
def grants():
    return {
        "grant_read": "id=read-permission-id,id=other-read-permission-id",
        "grant_read_acp": "id=read-acp-permission-id",
        "grant_write_acp": "id=write-acp-permission-id",
        "grant_full_control": "id=full-control-permission-id",
    }


def test_init(dvc):
    config = {"url": url}
    remote = S3Remote(dvc, config)

    assert remote.tree.path_info == url


def test_grants(dvc):
    config = {
        "url": url,
        "grant_read": "id=read-permission-id,id=other-read-permission-id",
        "grant_read_acp": "id=read-acp-permission-id",
        "grant_write_acp": "id=write-acp-permission-id",
        "grant_full_control": "id=full-control-permission-id",
    }
    remote = S3Remote(dvc, config)
    tree = remote.tree

    assert (
        tree.extra_args["GrantRead"]
        == "id=read-permission-id,id=other-read-permission-id"
    )
    assert tree.extra_args["GrantReadACP"] == "id=read-acp-permission-id"
    assert tree.extra_args["GrantWriteACP"] == "id=write-acp-permission-id"
    assert (
        tree.extra_args["GrantFullControl"] == "id=full-control-permission-id"
    )


def test_grants_mutually_exclusive_acl_error(dvc, grants):
    for grant_option, grant_value in grants.items():
        config = {"url": url, "acl": "public-read", grant_option: grant_value}

        with pytest.raises(ConfigError):
            S3Remote(dvc, config)


def test_sse_kms_key_id(dvc):
    remote = S3Remote(dvc, {"url": url, "sse_kms_key_id": "key"})
    assert remote.tree.extra_args["SSEKMSKeyId"] == "key"
