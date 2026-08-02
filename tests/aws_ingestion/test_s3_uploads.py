import base64

import pytest

from files.services.s3_uploads import (
    InvalidUploadObjectKey,
    S3Part,
    S3UploadGateway,
)


BUCKET = "private-media"
KEY = "uploads/job/session/source.mp4"
CHECKSUM = base64.b64encode(b"a" * 32).decode()


class RecordingS3Client:
    def __init__(self):
        self.calls = []
        self.list_responses = []

    def create_multipart_upload(self, **kwargs):
        self.calls.append(("create_multipart_upload", kwargs))
        return {"UploadId": "s3-upload"}

    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn, HttpMethod):
        self.calls.append(
            (
                "generate_presigned_url",
                {
                    "ClientMethod": ClientMethod,
                    "Params": Params,
                    "ExpiresIn": ExpiresIn,
                    "HttpMethod": HttpMethod,
                },
            )
        )
        return "https://signed.example.invalid/request"

    def list_parts(self, **kwargs):
        self.calls.append(("list_parts", kwargs))
        return self.list_responses.pop(0)

    def complete_multipart_upload(self, **kwargs):
        self.calls.append(("complete_multipart_upload", kwargs))
        return {"ETag": '"complete"'}

    def head_object(self, **kwargs):
        self.calls.append(("head_object", kwargs))
        return {
            "ContentLength": 42,
            "ContentType": "video/mp4",
            "ETag": '"object"',
            "ChecksumSHA256": f"{CHECKSUM}-1",
        }

    def abort_multipart_upload(self, **kwargs):
        self.calls.append(("abort_multipart_upload", kwargs))
        return {}

    def delete_objects(self, **kwargs):
        self.calls.append(("delete_objects", kwargs))
        return {"Deleted": kwargs["Delete"]["Objects"]}


@pytest.fixture
def client():
    return RecordingS3Client()


@pytest.fixture
def gateway(settings, client):
    settings.AWS_MEDIA_BUCKET = BUCKET
    settings.AWS_UPLOAD_PRESIGN_TTL_SECONDS = 900
    return S3UploadGateway(client)


def test_create_multipart_enables_sha256_composite_checksum(gateway, client):
    assert gateway.create_multipart(KEY, "video/mp4") == "s3-upload"
    assert client.calls == [
        (
            "create_multipart_upload",
            {
                "Bucket": BUCKET,
                "Key": KEY,
                "ContentType": "video/mp4",
                "ChecksumAlgorithm": "SHA256",
                "ChecksumType": "COMPOSITE",
            },
        )
    ]


def test_presigned_part_is_bound_to_exact_upload_and_checksum(gateway, client):
    signed = gateway.presign_part(KEY, "s3-upload", 7, CHECKSUM)

    assert signed.expires_in == 900
    assert signed.headers == {"x-amz-checksum-sha256": CHECKSUM}
    assert client.calls == [
        (
            "generate_presigned_url",
            {
                "ClientMethod": "upload_part",
                "Params": {
                    "Bucket": BUCKET,
                    "Key": KEY,
                    "UploadId": "s3-upload",
                    "PartNumber": 7,
                    "ChecksumSHA256": CHECKSUM,
                },
                "ExpiresIn": 900,
                "HttpMethod": "PUT",
            },
        )
    ]


def test_presigned_single_put_binds_type_length_and_checksum(gateway, client):
    signed = gateway.presign_put(KEY, "video/mp4", 42, CHECKSUM)

    assert signed.headers == {
        "content-type": "video/mp4",
        "content-length": "42",
        "x-amz-checksum-sha256": CHECKSUM,
    }
    assert client.calls[0][1]["Params"] == {
        "Bucket": BUCKET,
        "Key": KEY,
        "ContentType": "video/mp4",
        "ContentLength": 42,
        "ChecksumSHA256": CHECKSUM,
    }


def test_list_parts_paginates_and_preserves_authoritative_metadata(gateway, client):
    client.list_responses = [
        {
            "IsTruncated": True,
            "NextPartNumberMarker": 1,
            "Parts": [
                {
                    "PartNumber": 1,
                    "ETag": '"etag-1"',
                    "Size": 5,
                    "ChecksumSHA256": CHECKSUM,
                }
            ],
        },
        {
            "IsTruncated": False,
            "Parts": [
                {
                    "PartNumber": 2,
                    "ETag": '"etag-2"',
                    "Size": 3,
                    "ChecksumSHA256": CHECKSUM,
                }
            ],
        },
    ]

    assert gateway.list_parts(KEY, "s3-upload") == (
        S3Part(1, '"etag-1"', 5, CHECKSUM),
        S3Part(2, '"etag-2"', 3, CHECKSUM),
    )
    assert client.calls[1][1]["PartNumberMarker"] == 1


def test_complete_and_head_use_exact_evidence(gateway, client):
    parts = (S3Part(1, '"etag-1"', 42, CHECKSUM),)
    gateway.complete_multipart(KEY, "s3-upload", parts)
    evidence = gateway.head_object(KEY)

    assert client.calls[0][1]["MultipartUpload"] == {
        "Parts": [
            {
                "PartNumber": 1,
                "ETag": '"etag-1"',
                "ChecksumSHA256": CHECKSUM,
            }
        ]
    }
    assert evidence.size == 42
    assert evidence.content_type == "video/mp4"
    assert evidence.checksum_sha256 == f"{CHECKSUM}-1"
    assert client.calls[1] == (
        "head_object",
        {"Bucket": BUCKET, "Key": KEY, "ChecksumMode": "ENABLED"},
    )


def test_abort_and_delete_are_limited_to_exact_upload_keys(gateway, client):
    gateway.abort_multipart(KEY, "s3-upload")
    gateway.delete_exact_keys((KEY, "uploads/job/session/poster.jpg"))

    assert client.calls[0][0] == "abort_multipart_upload"
    assert client.calls[1][1]["Delete"]["Objects"] == [
        {"Key": KEY},
        {"Key": "uploads/job/session/poster.jpg"},
    ]


@pytest.mark.parametrize(
    "key",
    ["", "/uploads/a", "uploads/", "uploads/a/../b", "uploads/a//b", "other/a"],
)
def test_all_operations_reject_non_exact_or_out_of_scope_keys(gateway, key):
    with pytest.raises(InvalidUploadObjectKey):
        gateway.create_multipart(key, "video/mp4")


@pytest.mark.parametrize("part_number", [0, 10_001])
def test_presign_rejects_part_number_outside_s3_range(gateway, part_number):
    with pytest.raises(ValueError, match="part number"):
        gateway.presign_part(KEY, "s3-upload", part_number, CHECKSUM)


def test_presign_rejects_invalid_sha256_before_calling_s3(gateway, client):
    with pytest.raises(ValueError, match="SHA-256"):
        gateway.presign_part(KEY, "s3-upload", 1, "not-base64")
    assert client.calls == []
