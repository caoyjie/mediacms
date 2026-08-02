from dataclasses import replace

import pytest

from files.models import (
    BrowserUploadObject,
    BrowserUploadSession,
    Media,
    MediaIngestionJob,
    MediaJobAttempt,
    MediaJobCheckpoint,
)
from files.models.uploads import BrowserUploadPart
from files.services.processing_queue import acquire_head_job
from files.services.s3_uploads import PresignedRequest, S3ObjectEvidence, S3Part
from files.services.upload_lease import UploadLeaseConflict, acquire_upload_lease
from files.services.upload_sessions import (
    CreateFileSession,
    InvalidUploadCommand,
    PartUploadRequest,
    UploadIdempotencyConflict,
    UploadRevisionConflict,
    UploadVerificationFailed,
    cancel_upload,
    complete_file_upload,
    create_file_session,
    get_resume_snapshot,
    issue_part_urls,
    pause_upload,
    reconcile_parts,
    resume_upload,
)
from tests.users.factories import UserFactory


class RecordingUploadGateway:
    def __init__(self):
        self.create_calls = []
        self.presign_calls = []
        self.parts = ()
        self.complete_calls = []
        self.abort_calls = []
        self.delete_calls = []
        self.head_evidence = None
        self.head_error = None

    def create_multipart(self, key, content_type):
        self.create_calls.append((key, content_type))
        return "s3-upload-id"

    def presign_part(self, key, upload_id, part_number, checksum_sha256):
        self.presign_calls.append((key, upload_id, part_number, checksum_sha256))
        return PresignedRequest(
            url=f"https://signed.example.invalid/{part_number}",
            headers={"x-amz-checksum-sha256": checksum_sha256},
            expires_in=900,
        )

    def list_parts(self, key, upload_id):
        return self.parts

    def complete_multipart(self, key, upload_id, parts):
        self.complete_calls.append((key, upload_id, parts))

    def head_object(self, key):
        if self.head_error is not None:
            error = self.head_error
            self.head_error = None
            raise error
        return self.head_evidence

    def abort_multipart(self, key, upload_id):
        self.abort_calls.append((key, upload_id))

    def delete_exact_keys(self, keys):
        self.delete_calls.append(tuple(keys))


@pytest.fixture
def administrator(db):
    return UserFactory(is_staff=True, is_superuser=True)


@pytest.fixture
def gateway():
    return RecordingUploadGateway()


@pytest.fixture
def file_command():
    return CreateFileSession(
        title="Short clip",
        media_type="video",
        filename="clip.mp4",
        size=32_000_000,
        content_type="video/mp4",
        fingerprint="sha256:browser-fingerprint",
        idempotency_key="create-file-1",
    )


@pytest.mark.django_db
def test_create_file_session_builds_one_aws_draft_and_server_key(
    administrator,
    gateway,
    file_command,
):
    result = create_file_session(administrator, file_command, gateway)

    session = BrowserUploadSession.objects.get(pk=result.session_id)
    upload_object = BrowserUploadObject.objects.get(pk=result.object_id)
    media = Media.objects.get(pk=result.media_id)
    job = MediaIngestionJob.objects.get(pk=result.job_id)
    assert media.title == "Short clip"
    assert media.media_type == "video"
    assert media.storage_backend == "aws"
    assert media.processing_status == "draft"
    assert job.source_type == "upload"
    assert job.stage == "waiting_upload"
    assert session.job_id == job.id
    assert session.file_fingerprint == "sha256:browser-fingerprint"
    assert upload_object.relative_path == "source.mp4"
    assert upload_object.s3_key == f"{session.upload_prefix}source.mp4"
    assert upload_object.multipart_upload_id == "s3-upload-id"
    assert gateway.create_calls == [(upload_object.s3_key, "video/mp4")]


@pytest.mark.django_db
def test_create_file_session_is_idempotent(administrator, gateway, file_command):
    first = create_file_session(administrator, file_command, gateway)
    second = create_file_session(administrator, file_command, gateway)

    assert second == first
    assert Media.objects.filter(storage_backend="aws").count() == 1
    assert BrowserUploadSession.objects.count() == 1
    assert gateway.create_calls == [
        (BrowserUploadObject.objects.get().s3_key, "video/mp4")
    ]


@pytest.mark.django_db
def test_idempotency_key_reuse_with_changed_payload_conflicts(
    administrator,
    gateway,
    file_command,
):
    create_file_session(administrator, file_command, gateway)

    with pytest.raises(UploadIdempotencyConflict):
        create_file_session(
            administrator,
            replace(file_command, size=file_command.size + 1),
            gateway,
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "changes",
    [
        {"media_type": "image"},
        {"filename": "../clip.mp4"},
        {"filename": "clip.exe"},
        {"content_type": "application/octet-stream"},
        {"size": 0},
        {"title": ""},
        {"idempotency_key": ""},
    ],
)
def test_invalid_file_commands_fail_before_creating_state(
    administrator,
    gateway,
    file_command,
    changes,
):
    with pytest.raises(InvalidUploadCommand):
        create_file_session(administrator, replace(file_command, **changes), gateway)
    assert BrowserUploadSession.objects.count() == 0
    assert gateway.create_calls == []


@pytest.mark.django_db
def test_processing_worker_cannot_acquire_a_job_before_upload_completion(
    administrator,
    gateway,
    file_command,
):
    create_file_session(administrator, file_command, gateway)

    assert acquire_head_job("processing-worker", 60) is None


@pytest.mark.django_db
def test_part_urls_require_the_exact_upload_lease(administrator, gateway, file_command):
    created = create_file_session(administrator, file_command, gateway)
    request = PartUploadRequest(1, "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE=")

    with pytest.raises(UploadLeaseConflict):
        issue_part_urls(created.session_id, "browser-a", (request,), gateway)


@pytest.mark.django_db
def test_part_url_batch_is_bounded_and_bound_to_expected_parts(
    administrator,
    gateway,
    file_command,
):
    created = create_file_session(administrator, file_command, gateway)
    acquire_upload_lease(created.session_id, "browser-a", 60)
    checksum = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="

    signed = issue_part_urls(
        created.session_id,
        "browser-a",
        (PartUploadRequest(1, checksum), PartUploadRequest(2, checksum)),
        gateway,
    )

    upload_object = BrowserUploadObject.objects.get(pk=created.object_id)
    assert len(signed) == 2
    assert gateway.presign_calls == [
        (upload_object.s3_key, "s3-upload-id", 1, checksum),
        (upload_object.s3_key, "s3-upload-id", 2, checksum),
    ]
    with pytest.raises(InvalidUploadCommand, match="Part number"):
        issue_part_urls(
            created.session_id,
            "browser-a",
            (PartUploadRequest(3, checksum),),
            gateway,
        )
    with pytest.raises(InvalidUploadCommand, match="20"):
        issue_part_urls(
            created.session_id,
            "browser-a",
            tuple(PartUploadRequest(1, checksum) for _ in range(21)),
            gateway,
        )


@pytest.mark.django_db
def test_reconcile_replaces_browser_state_with_s3_authority(
    administrator,
    gateway,
    file_command,
):
    created = create_file_session(administrator, file_command, gateway)
    acquire_upload_lease(created.session_id, "browser-a", 60)
    upload_object = BrowserUploadObject.objects.get(pk=created.object_id)
    BrowserUploadPart.objects.create(
        upload_object=upload_object,
        part_number=1,
        etag='"browser-claim"',
        size=1,
        checksum_sha256="browser-checksum",
    )
    checksum = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
    gateway.parts = (S3Part(1, '"s3-etag"', 16_777_216, checksum),)

    snapshot = reconcile_parts(created.session_id, "browser-a", gateway)

    authoritative = BrowserUploadPart.objects.get(upload_object=upload_object)
    assert authoritative.etag == '"s3-etag"'
    assert authoritative.size == 16_777_216
    assert authoritative.checksum_sha256 == checksum
    assert snapshot.confirmed_bytes == 16_777_216
    assert snapshot.status == "uploading"
    assert snapshot.revision == 3


@pytest.mark.django_db
def test_resume_snapshot_refreshes_parts_from_s3_without_reusing_urls(
    administrator,
    gateway,
    file_command,
):
    created = create_file_session(administrator, file_command, gateway)
    checksum = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
    gateway.parts = (S3Part(1, '"s3-etag"', 16_777_216, checksum),)

    snapshot = get_resume_snapshot(created.session_id, gateway)

    assert snapshot.confirmed_bytes == 16_777_216
    assert snapshot.file_fingerprint == "sha256:browser-fingerprint"
    assert snapshot.confirmed_parts == (
        (1, '"s3-etag"', 16_777_216, checksum),
    )
    assert not hasattr(snapshot, "url")


def authoritative_parts():
    checksum = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
    return (
        S3Part(1, '"etag-1"', 16_777_216, checksum),
        S3Part(2, '"etag-2"', 15_222_784, checksum),
    )


@pytest.mark.django_db
def test_completion_uses_s3_evidence_and_only_queues_processing(
    administrator,
    gateway,
    file_command,
):
    created = create_file_session(administrator, file_command, gateway)
    acquire_upload_lease(created.session_id, "browser-a", 60)
    gateway.parts = authoritative_parts()
    reconcile_parts(created.session_id, "browser-a", gateway)
    gateway.head_evidence = S3ObjectEvidence(
        size=32_000_000,
        content_type="video/mp4",
        etag='"complete"',
        checksum_sha256="composite-checksum-2",
    )

    completed = complete_file_upload(
        created.session_id,
        "browser-a",
        "complete-file-1",
        expected_revision=3,
        gateway=gateway,
    )

    session = BrowserUploadSession.objects.get(pk=created.session_id)
    upload_object = BrowserUploadObject.objects.get(pk=created.object_id)
    job = MediaIngestionJob.objects.get(pk=created.job_id)
    media = Media.objects.get(pk=created.media_id)
    attempt = MediaJobAttempt.objects.get(job=job, sequence=1)
    checkpoint = MediaJobCheckpoint.objects.get(attempt=attempt, name="source_verified")
    assert completed.status == "completed"
    assert session.confirmed_bytes == 32_000_000
    assert session.confirmed_file_count == 1
    assert upload_object.status == "verified"
    assert upload_object.checksum == "composite-checksum-2"
    assert job.stage == "source_verified"
    assert job.status == "queued"
    assert media.processing_status == "queued"
    assert media.processing_status != "ready"
    assert checkpoint.status == "completed"
    assert checkpoint.evidence == {
        "object_id": str(upload_object.id),
        "size": 32_000_000,
        "content_type": "video/mp4",
        "checksum_sha256": "composite-checksum-2",
    }
    assert len(gateway.complete_calls) == 1
    assert acquire_head_job("processing-worker", 60).job_id == job.id


@pytest.mark.django_db
def test_completion_retry_returns_same_result_without_active_upload_lease(
    administrator,
    gateway,
    file_command,
):
    created = create_file_session(administrator, file_command, gateway)
    acquire_upload_lease(created.session_id, "browser-a", 60)
    gateway.parts = authoritative_parts()
    reconcile_parts(created.session_id, "browser-a", gateway)
    gateway.head_evidence = S3ObjectEvidence(
        32_000_000,
        "video/mp4",
        '"complete"',
        "composite-checksum-2",
    )
    first = complete_file_upload(
        created.session_id,
        "browser-a",
        "complete-file-1",
        3,
        gateway,
    )
    second = complete_file_upload(
        created.session_id,
        "browser-a",
        "complete-file-1",
        3,
        gateway,
    )

    assert second == first
    assert len(gateway.complete_calls) == 1


@pytest.mark.django_db
def test_completion_resumes_with_head_only_after_post_complete_interruption(
    administrator,
    gateway,
    file_command,
):
    created = create_file_session(administrator, file_command, gateway)
    acquire_upload_lease(created.session_id, "browser-a", 60)
    gateway.parts = authoritative_parts()
    reconcile_parts(created.session_id, "browser-a", gateway)
    gateway.head_error = RuntimeError("simulated connection loss")

    with pytest.raises(RuntimeError, match="connection loss"):
        complete_file_upload(
            created.session_id,
            "browser-a",
            "complete-file-1",
            3,
            gateway,
        )

    interrupted = BrowserUploadSession.objects.get(pk=created.session_id)
    assert interrupted.status == "verifying"
    assert interrupted.revision == 4
    gateway.head_evidence = S3ObjectEvidence(
        32_000_000,
        "video/mp4",
        '"complete"',
        "composite-checksum-2",
    )

    resumed = complete_file_upload(
        created.session_id,
        "browser-a",
        "complete-file-1",
        4,
        gateway,
    )

    assert resumed.status == "completed"
    assert len(gateway.complete_calls) == 1


@pytest.mark.django_db
def test_completion_rejects_stale_revision_and_size_mismatch(
    administrator,
    gateway,
    file_command,
):
    created = create_file_session(administrator, file_command, gateway)
    acquire_upload_lease(created.session_id, "browser-a", 60)
    gateway.parts = authoritative_parts()
    reconcile_parts(created.session_id, "browser-a", gateway)

    with pytest.raises(UploadRevisionConflict):
        complete_file_upload(
            created.session_id,
            "browser-a",
            "complete-file-1",
            2,
            gateway,
        )

    gateway.parts = (authoritative_parts()[0],)
    with pytest.raises(UploadVerificationFailed, match="size"):
        complete_file_upload(
            created.session_id,
            "browser-a",
            "complete-file-1",
            3,
            gateway,
        )
    assert gateway.complete_calls == []


@pytest.mark.django_db
def test_cancel_aborts_only_recorded_multipart_and_is_idempotent(
    administrator,
    gateway,
    file_command,
):
    created = create_file_session(administrator, file_command, gateway)
    acquire_upload_lease(created.session_id, "browser-a", 60)
    upload_object = BrowserUploadObject.objects.get(pk=created.object_id)

    first = cancel_upload(created.session_id, "browser-a", gateway)
    second = cancel_upload(created.session_id, "browser-a", gateway)

    assert second == first
    assert first.status == "canceled"
    assert gateway.abort_calls == [(upload_object.s3_key, "s3-upload-id")]
    assert gateway.delete_calls == []
    upload_object.refresh_from_db()
    assert upload_object.status == "aborted"
    assert MediaIngestionJob.objects.get(pk=created.job_id).status == "canceled"


@pytest.mark.django_db
def test_pause_releases_lease_and_resume_returns_session_to_fifo(
    administrator,
    gateway,
    file_command,
):
    created = create_file_session(administrator, file_command, gateway)
    acquire_upload_lease(created.session_id, "browser-a", 60)

    paused = pause_upload(created.session_id, "browser-a")

    assert paused.status == "paused"
    with pytest.raises(UploadLeaseConflict):
        issue_part_urls(
            created.session_id,
            "browser-a",
            (PartUploadRequest(1, "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="),),
            gateway,
        )
    resumed = resume_upload(created.session_id)
    assert resumed.status == "waiting"
    assert acquire_upload_lease(created.session_id, "browser-b", 60).session_id == created.session_id
