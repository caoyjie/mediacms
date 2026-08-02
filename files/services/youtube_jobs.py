"""Creation and recovery helpers for the single-video YouTube queue."""

from django.db import transaction
from django.utils import timezone

from files.models import Media, MediaIngestionJob
from files.models.domain import MediaProcessingStatus, StorageBackend, encoding_status_for
from files.models.ingestion import JobSourceType, JobStatus
from files.services.youtube import normalize_youtube_url


@transaction.atomic
def create_youtube_job(owner, url, *, title="YouTube video", idempotency_key=None):
    video_id = normalize_youtube_url(url)
    if idempotency_key:
        existing = MediaIngestionJob.objects.filter(
            source_type=JobSourceType.YOUTUBE,
            source_metadata__idempotency_key=idempotency_key,
        ).first()
        if existing:
            if existing.source_metadata.get("video_id") != video_id or existing.media.user_id != owner.id:
                raise ValueError("idempotency key was already used for another video")
            return existing
    media = Media.objects.create(
        title=title[:100],
        user=owner,
        media_type="video",
        storage_backend=StorageBackend.AWS,
        processing_status=MediaProcessingStatus.DRAFT,
        encoding_status=encoding_status_for(MediaProcessingStatus.DRAFT),
    )
    metadata = {"url": url, "video_id": video_id}
    if idempotency_key:
        metadata["idempotency_key"] = idempotency_key
    return MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot=media.title,
        source_type=JobSourceType.YOUTUBE,
        stage="metadata_pending",
        source_metadata=metadata,
    )


@transaction.atomic
def resume_youtube_job(job_id, *, cookie_version_id=None):
    job = MediaIngestionJob.objects.select_for_update().get(pk=job_id)
    if job.source_type != JobSourceType.YOUTUBE:
        raise ValueError("only YouTube jobs can be resumed by this operation")
    if job.status not in {JobStatus.FAILED, JobStatus.CANCELED}:
        raise ValueError("YouTube job is not waiting for resume")
    metadata = dict(job.source_metadata or {})
    if cookie_version_id:
        metadata["cookie_version_id"] = str(cookie_version_id)
    metadata.pop("action_required", None)
    job.source_metadata = metadata
    job.status = JobStatus.QUEUED
    job.stage = "metadata_pending"
    job.safe_error = ""
    job.cancel_requested = False
    job.queued_at = timezone.now()
    job.save(update_fields=("source_metadata", "status", "stage", "safe_error", "cancel_requested", "queued_at", "updated_at"))
    if job.media_id:
        Media.objects.filter(pk=job.media_id).exclude(processing_status=MediaProcessingStatus.READY).update(
            processing_status=MediaProcessingStatus.QUEUED,
            encoding_status=encoding_status_for(MediaProcessingStatus.QUEUED),
        )
    return job
