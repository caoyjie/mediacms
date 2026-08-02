from dataclasses import dataclass

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from files.models import (
    ArtifactCleanupStatus,
    ArtifactPurpose,
    AttemptArtifact,
    MediaAsset,
    MediaIngestionJob,
    MediaJobAttempt,
)
from files.models.ingestion import CleanupStatus


@dataclass(frozen=True, slots=True)
class CleanupResult:
    deleted: int
    retained: int
    failed: int


def cleanup_attempt(attempt_id, storage, *, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        attempt = MediaJobAttempt.objects.select_related("job").get(pk=attempt_id)
        job = MediaIngestionJob.objects.select_for_update().get(pk=attempt.job_id)
        job.cleanup_status = CleanupStatus.RUNNING
        job.save(update_fields=("cleanup_status", "updated_at"))
        artifacts = list(
            AttemptArtifact.objects.filter(attempt=attempt).exclude(
                cleanup_status=ArtifactCleanupStatus.RETAINED,
            ).filter(
                cleanup_status__in=(ArtifactCleanupStatus.PENDING, ArtifactCleanupStatus.FAILED),
            )
        )
        active_keys = set(
            MediaAsset.objects.filter(
                version__media_id=job.media_id,
                version__status="active",
                version__media__active_asset_version_id=F("version_id"),
            ).values_list("s3_key", flat=True)
        ) if job.media_id else set()
    deleted = retained = failed = 0
    for artifact in artifacts:
        if artifact.purpose == ArtifactPurpose.CANDIDATE and artifact.s3_key in active_keys:
            AttemptArtifact.objects.filter(pk=artifact.pk).update(
                cleanup_status=ArtifactCleanupStatus.RETAINED,
                safe_error="",
                updated_at=now,
            )
            retained += 1
            continue
        try:
            storage.delete_exact(artifact.s3_key)
        except Exception:
            AttemptArtifact.objects.filter(pk=artifact.pk).update(
                cleanup_status=ArtifactCleanupStatus.FAILED,
                safe_error="Storage cleanup failed; retry is required.",
                updated_at=now,
            )
            failed += 1
        else:
            AttemptArtifact.objects.filter(pk=artifact.pk).update(
                cleanup_status=ArtifactCleanupStatus.DELETED,
                safe_error="",
                updated_at=now,
            )
            deleted += 1
    with transaction.atomic():
        job = MediaIngestionJob.objects.select_for_update().get(pk=attempt.job_id)
        remaining = AttemptArtifact.objects.filter(
            attempt_id=attempt_id,
            cleanup_status__in=(ArtifactCleanupStatus.PENDING, ArtifactCleanupStatus.FAILED),
        ).exists()
        job.cleanup_status = CleanupStatus.FAILED if remaining else CleanupStatus.COMPLETED
        job.save(update_fields=("cleanup_status", "updated_at"))
    return CleanupResult(deleted, retained, failed)
