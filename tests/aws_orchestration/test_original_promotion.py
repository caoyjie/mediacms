import pytest
from botocore.exceptions import ClientError

from files.models import (
    ArtifactPurpose,
    AttemptArtifact,
    BrowserUploadObject,
    MediaIngestionJob,
    MediaJobAttempt,
    MediaJobCheckpoint,
)
from files.services.processing_queue import acquire_head_job
from files.services.processing_storage import ObjectEvidence
from files.services.s3_uploads import S3ObjectEvidence, S3Part
from files.services.upload_lease import acquire_upload_lease
from files.services.upload_sessions import (
    CreateFileSession,
    UploadVerificationFailed,
    complete_file_upload,
    create_file_session,
    promote_file_original,
    reconcile_parts,
)
from tests.aws_ingestion.test_upload_sessions import RecordingUploadGateway
from tests.users.factories import UserFactory


class RecordingPromotionStorage:
    def __init__(self):
        self.copy_calls = []
        self.head_calls = []
        self.head_evidence = None
        self.head_error = None
        self.copy_error = None

    def copy_exact(self, source_key, destination_key):
        self.copy_calls.append((source_key, destination_key))
        if self.copy_error is not None:
            error = self.copy_error
            self.copy_error = None
            raise error

    def head_exact(self, key):
        self.head_calls.append(key)
        if self.head_error is not None:
            error = self.head_error
            self.head_error = None
            raise error
        return self.head_evidence


@pytest.fixture
def prepared_upload(db):
    owner = UserFactory(is_staff=True, is_superuser=True)
    gateway = RecordingUploadGateway()
    created = create_file_session(
        owner,
        CreateFileSession(
            title="Promotion",
            media_type="video",
            filename="clip.mp4",
            size=6_000_000,
            content_type="video/mp4",
            fingerprint="sha256:promotion",
            idempotency_key="promotion-create",
        ),
        gateway,
    )
    acquire_upload_lease(created.session_id, "browser", 60)
    checksum = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
    gateway.parts = (S3Part(1, '"etag"', 6_000_000, checksum),)
    reconcile_parts(created.session_id, "browser", gateway)
    gateway.head_evidence = S3ObjectEvidence(
        size=6_000_000,
        content_type="video/mp4",
        etag='"complete"',
        checksum_sha256="source-composite-checksum-1",
    )
    return created, gateway


def destination_for(created):
    attempt = MediaJobAttempt.objects.get(job_id=created.job_id, sequence=1)
    return f"originals/{created.media_id}/{attempt.id}/source.mp4"


@pytest.mark.django_db
def test_completion_copies_to_attempt_original_before_checkpoint_and_queue(prepared_upload):
    created, upload_gateway = prepared_upload
    storage = RecordingPromotionStorage()

    def verified_head(key):
        assert MediaJobCheckpoint.objects.filter(name="source_verified").count() == 0
        assert acquire_head_job("too-early", 60) is None
        return ObjectEvidence(key, 6_000_000, "video/mp4", "original-sha256")

    storage.head_exact = lambda key: storage.head_calls.append(key) or verified_head(key)
    completed = complete_file_upload(
        created.session_id,
        "browser",
        "promotion-complete",
        3,
        upload_gateway,
        storage,
    )

    upload = BrowserUploadObject.objects.get(pk=created.object_id)
    destination = destination_for(created)
    checkpoint = MediaJobCheckpoint.objects.get(name="source_verified")
    assert completed.status == "completed"
    assert upload.promoted_s3_key == destination
    assert upload.promotion_status == "verified"
    assert storage.copy_calls == [(upload.s3_key, destination)]
    assert storage.head_calls == [destination]
    assert checkpoint.evidence == {
        "object_id": str(upload.id),
        "s3_key": destination,
        "size": 6_000_000,
        "content_type": "video/mp4",
        "checksum_sha256": "original-sha256",
    }
    assert set(
        AttemptArtifact.objects.filter(attempt=checkpoint.attempt).values_list(
            "purpose", "s3_key"
        )
    ) == {
        (ArtifactPurpose.UPLOAD_SOURCE, upload.s3_key),
        (ArtifactPurpose.ORIGINAL, destination),
    }
    assert acquire_head_job("processing-worker", 60).job_id == created.job_id


@pytest.mark.django_db
def test_promotion_head_mismatch_does_not_checkpoint_or_enqueue(prepared_upload):
    created, upload_gateway = prepared_upload
    storage = RecordingPromotionStorage()
    attempt = MediaJobAttempt.objects.create(
        job_id=created.job_id,
        sequence=1,
    )
    destination = f"originals/{created.media_id}/{attempt.id}/source.mp4"
    storage.head_evidence = ObjectEvidence(
        destination,
        5,
        "video/mp4",
        "original-sha256",
    )

    with pytest.raises(UploadVerificationFailed, match="size"):
        complete_file_upload(
            created.session_id,
            "browser",
            "promotion-complete",
            3,
            upload_gateway,
            storage,
        )

    upload = BrowserUploadObject.objects.get(pk=created.object_id)
    assert upload.promotion_status == "failed"
    assert not MediaJobCheckpoint.objects.filter(name="source_verified").exists()
    assert MediaIngestionJob.objects.get(pk=created.job_id).stage == "waiting_upload"
    assert acquire_head_job("processing-worker", 60) is None


@pytest.mark.django_db
def test_retry_heads_deterministic_original_without_recopy_after_interruption(prepared_upload):
    created, upload_gateway = prepared_upload
    storage = RecordingPromotionStorage()
    storage.head_error = RuntimeError("connection lost after copy")

    with pytest.raises(RuntimeError, match="connection lost"):
        complete_file_upload(
            created.session_id,
            "browser",
            "promotion-complete",
            3,
            upload_gateway,
            storage,
        )

    attempt_id = MediaJobAttempt.objects.get(job_id=created.job_id, sequence=1).id
    upload = BrowserUploadObject.objects.get(pk=created.object_id)
    destination = f"originals/{created.media_id}/{attempt_id}/source.mp4"
    assert upload.promotion_status == "copying"
    assert storage.copy_calls == [(upload.s3_key, destination)]
    storage.head_evidence = ObjectEvidence(
        destination,
        6_000_000,
        "video/mp4",
        "original-sha256",
    )

    completed = complete_file_upload(
        created.session_id,
        "browser",
        "promotion-complete",
        4,
        upload_gateway,
        storage,
    )

    assert completed.status == "completed"
    assert storage.copy_calls == [(upload.s3_key, destination)]
    assert storage.head_calls == [destination, destination]
    assert MediaJobAttempt.objects.filter(job_id=created.job_id).count() == 1


@pytest.mark.django_db
def test_retry_copies_only_after_head_proves_intended_original_is_missing(prepared_upload):
    created, upload_gateway = prepared_upload
    storage = RecordingPromotionStorage()
    storage.copy_error = RuntimeError("interrupted before copy")

    with pytest.raises(RuntimeError, match="before copy"):
        complete_file_upload(
            created.session_id,
            "browser",
            "promotion-complete",
            3,
            upload_gateway,
            storage,
        )

    destination = destination_for(created)
    storage.head_error = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
        "HeadObject",
    )
    storage.head_evidence = ObjectEvidence(
        destination,
        6_000_000,
        "video/mp4",
        "original-sha256",
    )

    completed = complete_file_upload(
        created.session_id,
        "browser",
        "promotion-complete",
        4,
        upload_gateway,
        storage,
    )

    assert completed.status == "completed"
    assert storage.copy_calls == [
        (BrowserUploadObject.objects.get(pk=created.object_id).s3_key, destination),
        (BrowserUploadObject.objects.get(pk=created.object_id).s3_key, destination),
    ]
    assert storage.head_calls == [destination, destination]


@pytest.mark.django_db
def test_promote_file_original_rejects_unverified_upload_source(prepared_upload):
    created, upload_gateway = prepared_upload
    storage = RecordingPromotionStorage()
    destination = f"originals/{created.media_id}/placeholder/source.mp4"
    storage.head_evidence = ObjectEvidence(destination, 6_000_000, "video/mp4", "sha256")

    with pytest.raises(UploadVerificationFailed):
        promote_file_original(created.session_id, storage)

    assert upload_gateway.complete_calls == []
