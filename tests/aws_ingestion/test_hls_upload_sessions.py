import pytest

from files.models import BrowserUploadObject, BrowserUploadSession, MediaIngestionJob, MediaJobCheckpoint
from files.services.hls_package import HlsInventoryEntry
from files.services.s3_uploads import PresignedRequest, S3ObjectEvidence
from files.services.upload_lease import acquire_upload_lease
from files.services.upload_sessions import (
    CreateHlsSession,
    InvalidUploadCommand,
    complete_hls_upload,
    create_hls_session,
    issue_hls_object_url,
    register_hls_inventory,
)
from tests.users.factories import UserFactory


CHECKSUM = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="


class HlsGateway:
    def __init__(self):
        self.create_calls = []
        self.put_calls = []
        self.head_evidence = {}

    def create_multipart(self, key, content_type):
        self.create_calls.append((key, content_type))
        return f"multipart-{len(self.create_calls)}"

    def presign_put(self, key, content_type, content_length, checksum):
        self.put_calls.append((key, content_type, content_length, checksum))
        return PresignedRequest("https://signed.example.invalid/put", {}, 900)

    def head_object(self, key):
        return self.head_evidence[key]


@pytest.fixture
def administrator(db):
    return UserFactory(is_staff=True, is_superuser=True)


@pytest.fixture
def gateway():
    return HlsGateway()


def hls_entries():
    return (
        HlsInventoryEntry("master.m3u8", 100, 50, "application/vnd.apple.mpegurl", CHECKSUM),
        HlsInventoryEntry("video/playlist.m3u8", 100, 50, "application/vnd.apple.mpegurl", CHECKSUM),
        HlsInventoryEntry("video/segment.ts", 1_000, 500, "video/mp2t", CHECKSUM),
    )


@pytest.mark.django_db
def test_hls_inventory_registration_generates_keys_and_size_strategies(administrator, gateway):
    created = create_hls_session(
        administrator,
        CreateHlsSession("Package", 20_001_200, 4, "package-fingerprint", "create-hls-1"),
    )
    acquire_upload_lease(created.session_id, "browser-a", 60)
    large = HlsInventoryEntry("video/large.m4s", 20_000_000, 10_000_000, "video/iso.segment", CHECKSUM)

    registered = register_hls_inventory(
        created.session_id,
        "browser-a",
        hls_entries() + (large,),
        gateway,
    )

    session = BrowserUploadSession.objects.get(pk=created.session_id)
    assert len(registered) == 4
    assert BrowserUploadObject.objects.filter(session=session, strategy="single_put").count() == 3
    large_object = BrowserUploadObject.objects.get(session=session, relative_path="video/large.m4s")
    assert large_object.strategy == "multipart"
    assert large_object.s3_key == f"{session.upload_prefix}video/large.m4s"
    assert large_object.multipart_upload_id == "multipart-1"


@pytest.mark.django_db
def test_hls_registration_is_idempotent_and_batches_are_bounded(administrator, gateway):
    created = create_hls_session(
        administrator,
        CreateHlsSession("Package", 1_200, 3, "package-fingerprint", "create-hls-1"),
    )
    acquire_upload_lease(created.session_id, "browser-a", 60)
    first = register_hls_inventory(created.session_id, "browser-a", hls_entries(), gateway)
    second = register_hls_inventory(created.session_id, "browser-a", hls_entries(), gateway)
    assert second == first
    assert BrowserUploadObject.objects.count() == 3
    with pytest.raises(InvalidUploadCommand, match="200"):
        register_hls_inventory(
            created.session_id,
            "browser-a",
            tuple(
                HlsInventoryEntry(f"{number}.ts", 1, 1, "video/mp2t", CHECKSUM)
                for number in range(201)
            ),
            gateway,
        )


@pytest.mark.django_db
def test_small_hls_object_gets_bound_single_put_url(administrator, gateway):
    created = create_hls_session(
        administrator,
        CreateHlsSession("Package", 1_200, 3, "package-fingerprint", "create-hls-1"),
    )
    acquire_upload_lease(created.session_id, "browser-a", 60)
    register_hls_inventory(created.session_id, "browser-a", hls_entries(), gateway)
    upload_object = BrowserUploadObject.objects.get(relative_path="video/segment.ts")

    signed = issue_hls_object_url(created.session_id, "browser-a", upload_object.id, gateway)

    assert signed.url.startswith("https://signed.example.invalid/")
    assert gateway.put_calls == [
        (upload_object.s3_key, "video/mp2t", 1_000, CHECKSUM)
    ]


@pytest.mark.django_db
def test_hls_completion_verifies_tree_and_records_entry_manifest(administrator, gateway):
    created = create_hls_session(
        administrator,
        CreateHlsSession("Package", 1_200, 3, "package-fingerprint", "create-hls-1"),
    )
    acquire_upload_lease(created.session_id, "browser-a", 60)
    register_hls_inventory(created.session_id, "browser-a", hls_entries(), gateway)
    for upload_object in BrowserUploadObject.objects.all():
        gateway.head_evidence[upload_object.s3_key] = S3ObjectEvidence(
            upload_object.expected_size,
            upload_object.content_type,
            '"etag"',
            CHECKSUM,
        )

    result = complete_hls_upload(
        created.session_id,
        "browser-a",
        "complete-hls-1",
        expected_revision=3,
        manifest_bodies={
            "master.m3u8": "#EXTM3U\nvideo/playlist.m3u8\n",
            "video/playlist.m3u8": "#EXTM3U\nsegment.ts\n#EXT-X-ENDLIST\n",
        },
        gateway=gateway,
    )

    assert result.status == "completed"
    job = MediaIngestionJob.objects.get(pk=created.job_id)
    assert job.stage == "source_verified"
    checkpoint = MediaJobCheckpoint.objects.get(attempt__job=job, name="source_verified")
    assert checkpoint.evidence["entry_manifest"] == "master.m3u8"
    assert set(checkpoint.evidence["closure_paths"]) == {
        "master.m3u8",
        "video/playlist.m3u8",
        "video/segment.ts",
    }
    assert all(upload_object.status == "verified" for upload_object in BrowserUploadObject.objects.all())
