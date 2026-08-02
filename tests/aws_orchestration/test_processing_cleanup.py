import pytest

from files.models import (
    ArtifactCleanupStatus,
    ArtifactPurpose,
    AttemptArtifact,
    Media,
    MediaAsset,
    MediaAssetVersion,
    MediaIngestionJob,
    MediaJobAttempt,
)
from files.services.processing_cleanup import cleanup_attempt
from tests.users.factories import UserFactory


class CleanupStorage:
    def __init__(self, failures=()):
        self.failures = set(failures)
        self.deleted = []

    def delete_exact(self, key):
        self.deleted.append(key)
        if key in self.failures:
            raise RuntimeError("temporary storage failure")


@pytest.fixture
def cleanup_case(db):
    owner = UserFactory(is_staff=True, is_superuser=True)
    media = Media.objects.create(
        title="Cleanup",
        user=owner,
        media_type="video",
        storage_backend="aws",
        processing_status="ready",
        encoding_status="success",
    )
    job = MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot=media.title,
        source_type="upload",
        status="completed",
        cleanup_status="pending",
    )
    attempt = MediaJobAttempt.objects.create(job=job, sequence=1, status="completed")
    return media, job, attempt


def artifact(attempt, purpose, key, status=ArtifactCleanupStatus.PENDING):
    return AttemptArtifact.objects.create(
        attempt=attempt,
        purpose=purpose,
        s3_key=key,
        size_bytes=10,
        content_type="video/mp4",
        checksum="sha256:test",
        cleanup_status=status,
    )


@pytest.mark.django_db
def test_success_cleanup_deletes_sources_and_retains_active_candidate(cleanup_case):
    media, job, attempt = cleanup_case
    artifact(attempt, ArtifactPurpose.UPLOAD_SOURCE, "uploads/u/source.mp4")
    artifact(attempt, ArtifactPurpose.ORIGINAL, "originals/m/o.mp4")
    active = MediaAssetVersion.objects.create(
        media=media,
        attempt=attempt,
        status=MediaAssetVersion.Status.ACTIVE,
        manifest_key="candidates/m/a/hls/master.m3u8",
    )
    MediaAsset.objects.create(
        version=active,
        kind=MediaAsset.Kind.HLS_MASTER,
        s3_key=active.manifest_key,
        checksum="sha256:active",
        size_bytes=10,
        content_type="application/vnd.apple.mpegurl",
    )
    Media.objects.filter(pk=media.pk).update(active_asset_version=active)
    artifact(attempt, ArtifactPurpose.CANDIDATE, active.manifest_key)
    storage = CleanupStorage()

    result = cleanup_attempt(attempt.id, storage)

    job.refresh_from_db()
    assert result.failed == 0
    assert storage.deleted == ["uploads/u/source.mp4", "originals/m/o.mp4"]
    assert job.cleanup_status == "completed"
    assert AttemptArtifact.objects.get(s3_key=active.manifest_key).cleanup_status == ArtifactCleanupStatus.RETAINED
    media.refresh_from_db()
    assert media.processing_status == "ready"


@pytest.mark.django_db
def test_failed_attempt_deletes_candidate_and_original_but_keeps_previous_active(cleanup_case):
    media, _, attempt = cleanup_case
    artifact(attempt, ArtifactPurpose.ORIGINAL, "originals/m/o.mp4")
    artifact(attempt, ArtifactPurpose.CANDIDATE, "candidates/m/a/hls/master.m3u8")
    previous = MediaAssetVersion.objects.create(
        media=media,
        status=MediaAssetVersion.Status.ACTIVE,
        manifest_key="candidates/m/old/hls/master.m3u8",
    )
    MediaAsset.objects.create(
        version=previous,
        kind=MediaAsset.Kind.HLS_MASTER,
        s3_key=previous.manifest_key,
        checksum="sha256:old",
        size_bytes=10,
        content_type="application/vnd.apple.mpegurl",
    )
    Media.objects.filter(pk=media.pk).update(active_asset_version=previous)
    storage = CleanupStorage()

    cleanup_attempt(attempt.id, storage)

    assert storage.deleted == ["originals/m/o.mp4", "candidates/m/a/hls/master.m3u8"]
    media.refresh_from_db()
    assert media.active_asset_version_id == previous.id
    assert media.processing_status == "ready"


@pytest.mark.django_db
def test_cleanup_failure_is_persisted_and_retry_only_visits_failed_artifacts(cleanup_case):
    _, job, attempt = cleanup_case
    failed_key = "originals/m/fail.mp4"
    done_key = "originals/m/done.mp4"
    artifact(attempt, ArtifactPurpose.ORIGINAL, failed_key)
    artifact(attempt, ArtifactPurpose.ORIGINAL, done_key)
    storage = CleanupStorage({failed_key})

    first = cleanup_attempt(attempt.id, storage)
    storage.failures.clear()
    second = cleanup_attempt(attempt.id, storage)

    job.refresh_from_db()
    assert first.failed == 1
    assert second.failed == 0
    assert storage.deleted == [failed_key, done_key, failed_key]
    assert job.cleanup_status == "completed"
