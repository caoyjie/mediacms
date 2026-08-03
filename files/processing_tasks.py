from dataclasses import asdict

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from files.models import MediaIngestionJob, MediaJobAttempt, MediaJobCheckpoint
from files.models.ingestion import AttemptStatus, CheckpointStatus, JobStatus
from files.services.processing_runner import reconcile_processing, run_processing_tick
from files.services.youtube_import import discover
from files.services.youtube import choose_caption_tracks, discovered_caption_tracks
import files.services.processing_runner as processing_runner


@shared_task(name="aws_processing_tick")
def aws_processing_tick(job_id=None):
    owner_token = f"aws-processing:{job_id or 'reconciler'}"
    result = run_processing_tick(owner_token)
    if result.scheduled_delay is not None and result.action != "idle":
        aws_processing_tick.apply_async(
            args=((result.job_id or job_id),),
            countdown=max(0, result.scheduled_delay),
        )
    return {
        "action": result.action,
        "job_id": result.job_id,
        "attempt_id": result.attempt_id,
        "scheduled_delay": result.scheduled_delay,
    }


@shared_task(name="reconcile_aws_processing")
def reconcile_aws_processing():
    processing_runner.aws_processing_tick = aws_processing_tick
    return asdict(reconcile_processing())


@shared_task(name="discover_youtube_metadata", queue="youtube_metadata")
def discover_youtube_metadata(job_id):
    """Discover YouTube metadata without taking the serialized import lease."""
    with transaction.atomic():
        job = MediaIngestionJob.objects.select_for_update().get(pk=job_id)
        if job.source_type != "youtube" or (job.source_metadata or {}).get("import_requested"):
            return {"job_id": str(job.id), "stage": job.stage, "skipped": True}
        attempt = (
            MediaJobAttempt.objects.select_for_update()
            .filter(job=job, sequence=1)
            .first()
        )
        if attempt is None:
            attempt = MediaJobAttempt.objects.create(job=job, sequence=1, status=AttemptStatus.QUEUED)
        if MediaJobCheckpoint.objects.filter(attempt=attempt, name="metadata").exists():
            return {"job_id": str(job.id), "stage": "metadata_ready", "skipped": True}
        url = (job.source_metadata or {}).get("url")

    try:
        metadata, info = discover(url)
    except Exception as error:
        kind = getattr(error, "kind", "unknown")
        safe_error = "YouTube download requires updated cookies." if kind == "cookies" else "YouTube metadata discovery failed."
        with transaction.atomic():
            MediaJobAttempt.objects.filter(pk=attempt.pk).update(
                status=AttemptStatus.FAILED,
                diagnostic_error=safe_error,
                completed_at=timezone.now(),
            )
            MediaIngestionJob.objects.filter(pk=job.id).update(
                status=JobStatus.FAILED,
                stage="action_required" if kind == "cookies" else "failed",
                safe_error=safe_error,
            )
        raise

    now = timezone.now()
    with transaction.atomic():
        job = MediaIngestionJob.objects.select_for_update().get(pk=job.id)
        attempt = MediaJobAttempt.objects.select_for_update().get(pk=attempt.pk)
        if not MediaJobCheckpoint.objects.filter(attempt=attempt, name="metadata").exists():
            MediaJobCheckpoint.objects.create(
                attempt=attempt,
                name="metadata",
                status=CheckpointStatus.COMPLETED,
                evidence={
                    "video_id": metadata.video_id,
                    "title": metadata.title,
                    "duration": metadata.duration,
                    "thumbnail": metadata.thumbnail,
                    "caption_tracks": {
                        language: {"url": track.url, "kind": track.kind}
                        for language, track in choose_caption_tracks(discovered_caption_tracks(info)).items()
                    },
                },
                completed_at=now,
            )
            source_metadata = dict(job.source_metadata or {})
            source_metadata["discovered"] = {
                "video_id": metadata.video_id,
                "title": metadata.title,
                "description": metadata.description,
                "duration": metadata.duration,
                "thumbnail": metadata.thumbnail,
            }
            MediaIngestionJob.objects.filter(pk=job.id).update(
                source_metadata=source_metadata,
                media_title_snapshot=metadata.title,
                stage="metadata_ready",
            )
            if job.media_id and job.media and not (job.media.metadata_sources or {}).get("title"):
                job.media.title = metadata.title
                job.media.description = metadata.description
                job.media.duration = metadata.duration
                job.media.metadata_sources = {"title": "youtube", "description": "youtube", "duration": "youtube"}
                job.media.save(update_fields=("title", "description", "duration", "metadata_sources", "edit_date"))
    return {"job_id": str(job.id), "stage": "metadata_ready", "skipped": False}
