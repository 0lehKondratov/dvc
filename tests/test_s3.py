import uuid
import posixpath

import boto3
import pytest

from dvc.remote.s3 import RemoteS3
from tests.test_data_cloud import TEST_AWS_REPO_BUCKET, _should_test_aws


def _get_src_dst():
    prefix = str(uuid.uuid4())
    from_info = {
        "scheme": "s3",
        "bucket": TEST_AWS_REPO_BUCKET,
        "path": posixpath.join(prefix, "from"),
    }
    to_info = from_info.copy()
    to_info["path"] = posixpath.join(prefix, "to")
    return from_info, to_info


def test_copy_singlepart_preserve_etag():
    from_info, to_info = _get_src_dst()

    if not _should_test_aws():
        pytest.skip()

    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=from_info["bucket"], Key=from_info["path"], Body="data"
    )

    RemoteS3._copy(s3, from_info, to_info)


def _upload_multipart(s3, Bucket, Key):
    mpu = s3.create_multipart_upload(Bucket=Bucket, Key=Key)
    mpu_id = mpu["UploadId"]

    parts = []
    n_parts = 10
    for i in range(1, n_parts + 1):
        # NOTE: Generation parts of variable size. Part size should be at
        # least 5MB:
        # https://docs.aws.amazon.com/AmazonS3/latest/API/mpUploadComplete.html
        part_size = 10 * 1024 * 1024 + 2 ** i
        body = str(i) * part_size
        part = s3.upload_part(
            Bucket=Bucket,
            Key=Key,
            PartNumber=i,
            UploadId=mpu_id,
            Body=body,
            ContentLength=len(body),
        )
        parts.append({"PartNumber": i, "ETag": part["ETag"]})

    s3.complete_multipart_upload(
        Bucket=Bucket,
        Key=Key,
        UploadId=mpu_id,
        MultipartUpload={"Parts": parts},
    )


def test_copy_multipart_preserve_etag():
    from_info, to_info = _get_src_dst()

    if not _should_test_aws():
        pytest.skip()

    s3 = boto3.client("s3")
    _upload_multipart(s3, from_info["bucket"], from_info["path"])
    RemoteS3._copy(s3, from_info, to_info)
