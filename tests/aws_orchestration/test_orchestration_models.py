import pytest
from django.db import IntegrityError, transaction

from files.models import (
    ArtifactCleanupStatus,
    ArtifactPurpose,
    AttemptArtifact,
    MediaIngestionJob,
    MediaJobAttempt,
    MediaJobWarning,
)
from files.models.uploads import (
    BrowserUploadObject,
    BrowserUploadSession,
    BrowserUploadStrategy,
    PromotionStatus,
)
from tests.users.factories import UserFactory


@pytest.fixture
def attempt(db):
    job = MediaIngestionJob.objects.create(
        media_title_snapshot="Orchestration",
        source_type="upload",
    )
    return MediaJobAttempt.objects.create(job=job, sequence=1)


@pytest.mark.django_db
def test_attempt_defaults_preserve_submission_and_polling_recovery_state(attempt):
    assert attempt.template_name == ""
    assert attempt.template_version == ""
    assert attempt.client_request_token == ""
    assert attempt.submission_intent_at is None
    assert attempt.next_poll_at is None
    assert attempt.provider_last_changed_at is None
    assert attempt.provider_unchanged_count == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("purpose", "s3_key"),
    [
        (ArtifactPurpose.UPLOAD_SOURCE, "uploads/job/session/source.mp4"),
        (ArtifactPurpose.ORIGINAL, "originals/media/attempt/source.mp4"),
        (ArtifactPurpose.CANDIDATE, "candidates/media/attempt/hls/master.m3u8"),
    ],
)
def test_artifact_ledger_accepts_only_managed_roots(attempt, purpose, s3_key):
    artifact = AttemptArtifact.objects.create(
        attempt=attempt,
        purpose=purpose,
        s3_key=s3_key,
        size_bytes=123,
        content_type="application/octet-stream",
        checksum="sha256:test",
    )

    assert artifact.cleanup_status == ArtifactCleanupStatus.PENDING
    assert str(artifact) == f"{attempt.id}:{purpose}:{artifact.cleanup_status}"


@pytest.mark.django_db
def test_artifact_ledger_rejects_keys_outside_managed_roots(attempt):
    with pytest.raises(IntegrityError), transaction.atomic():
        AttemptArtifact.objects.create(
            attempt=attempt,
            purpose=ArtifactPurpose.CANDIDATE,
            s3_key="system/defaults/poster.jpg",
            size_bytes=123,
            content_type="image/jpeg",
            checksum="sha256:test",
        )


@pytest.mark.django_db
def test_artifact_key_is_unique_within_attempt(attempt):
    values = {
        "attempt": attempt,
        "purpose": ArtifactPurpose.ORIGINAL,
        "s3_key": "originals/media/attempt/source.mp4",
        "size_bytes": 123,
        "content_type": "video/mp4",
        "checksum": "sha256:test",
    }
    AttemptArtifact.objects.create(**values)

    with pytest.raises(IntegrityError), transaction.atomic():
        AttemptArtifact.objects.create(**values)


@pytest.mark.django_db
def test_warning_code_is_unique_within_attempt_and_string_omits_message(attempt):
    warning = MediaJobWarning.objects.create(
        attempt=attempt,
        code="mediaconvert_stalled",
        message="Safe administrator guidance",
    )

    assert str(warning) == f"{attempt.id}:mediaconvert_stalled"
    assert "Safe administrator guidance" not in str(warning)
    with pytest.raises(IntegrityError), transaction.atomic():
        MediaJobWarning.objects.create(
            attempt=attempt,
            code="mediaconvert_stalled",
            message="Duplicate",
        )


@pytest.mark.django_db
def test_upload_object_defaults_to_pending_promotion_state():
    owner = UserFactory(is_staff=True, is_superuser=True)
    job = MediaIngestionJob.objects.create(media_title_snapshot="Upload", source_type="upload")
    session = BrowserUploadSession.objects.create(
        job=job,
        owner=owner,
        source_kind="file",
        expected_total_size=1024,
        create_idempotency_key="promotion-test",
    )
    upload = BrowserUploadObject.objects.create(
        session=session,
        relative_path="source.mp4",
        s3_key=f"{session.upload_prefix}source.mp4",
        strategy=BrowserUploadStrategy.SINGLE_PUT,
        expected_size=1024,
        content_type="video/mp4",
    )

    assert upload.promoted_s3_key == ""
    assert upload.promotion_status == PromotionStatus.PENDING
    assert set(PromotionStatus.values) == {"pending", "copying", "verified", "failed"}


def test_attempt_has_due_tick_database_index():
    assert any(
        tuple(index.fields) == ("status", "next_poll_at")
        for index in MediaJobAttempt._meta.indexes
    )
