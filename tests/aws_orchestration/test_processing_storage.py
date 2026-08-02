import base64
from io import BytesIO

import pytest
from botocore.exceptions import ClientError

from files.services.processing_storage import (
    InvalidManagedObject,
    InvalidObjectEvidence,
    ObjectEvidence,
    ProcessingStorageGateway,
)


BUCKET = "mediacms-123456789012-us-east-1"
CHECKSUM = base64.b64encode(b"a" * 32).decode()


class RecordingS3Client:
    def __init__(self):
        self.calls = []
        self.list_responses = []
        self.get_response = {
            "ContentLength": 16,
            "Body": BytesIO(b"#EXTM3U\nvalue\n"),
        }

    def copy_object(self, **kwargs):
        self.calls.append(("copy_object", kwargs))
        return {}

    def head_object(self, **kwargs):
        self.calls.append(("head_object", kwargs))
        return {
            "ContentLength": 42,
            "ContentType": "video/mp4",
            "ChecksumSHA256": CHECKSUM,
        }

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
        return "https://signed.example.invalid/object"

    def get_object(self, **kwargs):
        self.calls.append(("get_object", kwargs))
        return self.get_response

    def list_objects_v2(self, **kwargs):
        self.calls.append(("list_objects_v2", kwargs))
        return self.list_responses.pop(0)

    def delete_object(self, **kwargs):
        self.calls.append(("delete_object", kwargs))
        return {}


@pytest.fixture
def client():
    return RecordingS3Client()


@pytest.fixture
def gateway(settings, client):
    settings.AWS_MEDIA_BUCKET = BUCKET
    settings.AWS_PROCESSING_PRESIGN_TTL_SECONDS = 300
    settings.AWS_MANIFEST_MAX_BYTES = 1024 * 1024
    settings.AWS_CANDIDATE_LIST_MAX_OBJECTS = 10_000
    return ProcessingStorageGateway(client=client)


def test_copy_uses_exact_same_bucket_keys_and_preserves_metadata(gateway, client):
    gateway.copy_exact(
        "uploads/job/session/source.mp4",
        "originals/media/attempt/source.mp4",
    )

    assert client.calls == [
        (
            "copy_object",
            {
                "Bucket": BUCKET,
                "Key": "originals/media/attempt/source.mp4",
                "CopySource": {
                    "Bucket": BUCKET,
                    "Key": "uploads/job/session/source.mp4",
                },
                "MetadataDirective": "COPY",
                "ChecksumAlgorithm": "SHA256",
            },
        )
    ]


def test_head_returns_immutable_checksum_evidence(gateway, client):
    evidence = gateway.head_exact("originals/media/attempt/source.mp4")

    assert evidence == ObjectEvidence(
        key="originals/media/attempt/source.mp4",
        size=42,
        content_type="video/mp4",
        checksum_sha256=CHECKSUM,
    )
    with pytest.raises(AttributeError):
        evidence.size = 1
    assert client.calls == [
        (
            "head_object",
            {
                "Bucket": BUCKET,
                "Key": "originals/media/attempt/source.mp4",
                "ChecksumMode": "ENABLED",
            },
        )
    ]


def test_presign_get_is_sigv4_bound_to_configured_bucket(gateway, client):
    signed = gateway.presign_get("candidates/media/attempt/hls/master.m3u8")

    assert signed == "https://signed.example.invalid/object"
    assert client.calls == [
        (
            "generate_presigned_url",
            {
                "ClientMethod": "get_object",
                "Params": {
                    "Bucket": BUCKET,
                    "Key": "candidates/media/attempt/hls/master.m3u8",
                },
                "ExpiresIn": 300,
                "HttpMethod": "GET",
            },
        )
    ]


def test_get_text_reads_bounded_utf8_manifest(gateway, client):
    assert gateway.get_text("candidates/media/attempt/hls/master.m3u8") == "#EXTM3U\nvalue\n"
    assert client.calls == [
        (
            "get_object",
            {
                "Bucket": BUCKET,
                "Key": "candidates/media/attempt/hls/master.m3u8",
                "Range": "bytes=0-1048576",
            },
        )
    ]


def test_get_text_rejects_oversized_or_non_utf8_content(gateway, client):
    client.get_response = {
        "ContentLength": 1024 * 1024 + 1,
        "Body": BytesIO(b"x" * (1024 * 1024 + 1)),
    }
    with pytest.raises(InvalidObjectEvidence, match="size limit"):
        gateway.get_text("candidates/media/attempt/hls/master.m3u8")

    client.get_response = {"ContentLength": 1, "Body": BytesIO(b"\xff")}
    with pytest.raises(InvalidObjectEvidence, match="UTF-8"):
        gateway.get_text("candidates/media/attempt/hls/master.m3u8")


def test_candidate_listing_is_prefix_bounded_and_paginated(gateway, client):
    prefix = "candidates/media/attempt/"
    client.list_responses = [
        {
            "IsTruncated": True,
            "NextContinuationToken": "page-2",
            "Contents": [{"Key": f"{prefix}hls/master.m3u8"}],
        },
        {
            "IsTruncated": False,
            "Contents": [{"Key": f"{prefix}hls/segment-1.ts"}],
        },
    ]

    assert gateway.list_attempt_candidates(prefix) == (
        f"{prefix}hls/master.m3u8",
        f"{prefix}hls/segment-1.ts",
    )
    assert client.calls == [
        ("list_objects_v2", {"Bucket": BUCKET, "Prefix": prefix}),
        (
            "list_objects_v2",
            {"Bucket": BUCKET, "Prefix": prefix, "ContinuationToken": "page-2"},
        ),
    ]


def test_candidate_listing_rejects_missing_marker_or_excess_objects(gateway, client, settings):
    prefix = "candidates/media/attempt/"
    client.list_responses = [{"IsTruncated": True, "Contents": []}]
    with pytest.raises(InvalidObjectEvidence, match="continuation"):
        gateway.list_attempt_candidates(prefix)

    settings.AWS_CANDIDATE_LIST_MAX_OBJECTS = 1
    limited = ProcessingStorageGateway(client=client)
    client.list_responses = [
        {
            "IsTruncated": False,
            "Contents": [
                {"Key": f"{prefix}one.ts"},
                {"Key": f"{prefix}two.ts"},
            ],
        }
    ]
    with pytest.raises(InvalidObjectEvidence, match="object limit"):
        limited.list_attempt_candidates(prefix)


def test_delete_treats_not_found_as_success(gateway, client):
    client.delete_object = lambda **kwargs: (_ for _ in ()).throw(
        ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
            "DeleteObject",
        )
    )

    gateway.delete_exact("originals/media/attempt/source.mp4")


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/uploads/job/source.mp4",
        "uploads/",
        "uploads/job/../source.mp4",
        "originals/job//source.mp4",
        "system/defaults/poster.jpg",
        "s3://foreign-bucket/originals/media/attempt/source.mp4",
        "https://example.invalid/candidates/media/attempt/master.m3u8",
    ],
)
def test_all_operations_reject_traversal_foreign_bucket_and_unmanaged_keys(gateway, client, key):
    with pytest.raises(InvalidManagedObject):
        gateway.head_exact(key)
    assert client.calls == []


@pytest.mark.parametrize(
    "prefix",
    [
        "candidates/media/attempt",
        "candidates/media/",
        "originals/media/attempt/",
        "candidates/media/attempt/../foreign/",
    ],
)
def test_candidate_listing_requires_one_exact_attempt_prefix(gateway, client, prefix):
    with pytest.raises(InvalidManagedObject):
        gateway.list_attempt_candidates(prefix)
    assert client.calls == []
