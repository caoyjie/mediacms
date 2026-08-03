from dataclasses import asdict, dataclass

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from files.models import MediaIngestionJob, MediaJobAttempt, MediaJobCheckpoint
from files.models.ingestion import AttemptStatus, CheckpointStatus, CleanupStatus, JobStatus
from files.services.asset_publishing import attach_subtitle_assets, publish_candidate, register_candidate
from files.services.mediaconvert import MediaConvertGateway
from files.services.media_probe import SourceFacts, probe_source
from files.services.processing_cancellation import (
    reconcile_cancellation,
    request_attempt_cancel,
)
from files.services.processing_cleanup import cleanup_attempt
from files.services.processing_polling import poll_attempt
from files.services.processing_queue import (
    acquire_head_job,
    heartbeat_lease,
    release_lease,
)
from files.services.processing_storage import ProcessingStorageGateway
from files.services.processing_submission import (
    SubmissionOutcomeUnknown,
    prepare_submission,
    reconcile_unknown_submission,
    submit_prepared,
)
from files.services.output_verification import verify_mediaconvert_outputs
from files.services.youtube_import import run_youtube_step


@dataclass(frozen=True, slots=True)
class TickResult:
    action: str
    job_id: str | None = None
    attempt_id: str | None = None
    scheduled_delay: int | None = None
    terminal: bool = False


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    wakeups: int


def _lease_seconds():
    return int(getattr(settings, "AWS_PROCESSING_LEASE_SECONDS", 120))


def _source_facts(checkpoint):
    try:
        return SourceFacts(**checkpoint.evidence)
    except (TypeError, ValueError) as error:
        raise ValueError("Source probe checkpoint evidence is invalid.") from error


def _checkpoint(attempt, name, status=CheckpointStatus.COMPLETED):
    return MediaJobCheckpoint.objects.filter(
        attempt=attempt,
        name=name,
        status=status,
    ).first()


def _attempt_for_lease(acquisition):
    return (
        MediaJobAttempt.objects.select_related("job__media")
        .get(pk=acquisition.attempt_id)
    )


def _schedule_for(action):
    if action in {"poll", "wait", "cancel_requested"}:
        return 10
    if action in {"cleanup", "publish", "verify_outputs", "submit", "prepare_submission", "probe", "youtube_metadata", "youtube_download", "youtube_subtitles"}:
        return 0
    return None


def _run_action(attempt, media_gateway, storage_gateway, now):
    job = attempt.job
    if job.cancel_requested:
        if attempt.status in {AttemptStatus.FAILED, AttemptStatus.CANCELED, AttemptStatus.COMPLETED}:
            if job.cleanup_status in {CleanupStatus.PENDING, CleanupStatus.FAILED}:
                cleanup_attempt(attempt.id, storage_gateway, now=now)
                return "cleanup", True
            return "done", True
        if attempt.mediaconvert_job_id:
            if attempt.provider_status in {"CANCELED", "ERROR", "COMPLETE"}:
                reconcile_cancellation(attempt.id, media_gateway, now=now)
            else:
                request_attempt_cancel(attempt.id, media_gateway, now=now)
            return "cancel_requested", False
        request_attempt_cancel(attempt.id, media_gateway, now=now)
        return "cancel_requested", False

    if job.source_type == "youtube":
        youtube_action = run_youtube_step(attempt, now=now)
        if youtube_action != "ready":
            return f"youtube_{youtube_action}", youtube_action == "failed"

    source_verified = _checkpoint(attempt, "source_verified")
    if source_verified is None:
        return "wait", False
    source_probed = _checkpoint(attempt, "source_probed")
    if source_probed is None:
        key = source_verified.evidence.get("s3_key")
        if not isinstance(key, str) or not key:
            raise ValueError("Source verification evidence has no S3 key.")
        facts = probe_source(f"s3://{settings.AWS_MEDIA_BUCKET}/{key}", media_gateway)
        MediaJobCheckpoint.objects.update_or_create(
            attempt=attempt,
            name="source_probed",
            defaults={
                "status": CheckpointStatus.COMPLETED,
                "evidence": asdict(facts),
                "completed_at": now,
            },
        )
        MediaIngestionJob.objects.filter(pk=job.pk).update(stage="source_probed")
        return "probe", False

    submitting = _checkpoint(attempt, "mediaconvert_submitting")
    if submitting is None:
        prepare_submission(attempt.id, _source_facts(source_probed))
        return "prepare_submission", False

    if not attempt.mediaconvert_job_id:
        coordination = attempt.checkpoint_evidence.get("mediaconvert_submission", {})
        try:
            if coordination.get("create_attempts"):
                reconcile_unknown_submission(attempt.id, media_gateway, now)
                return "reconcile_submission", False
            submit_prepared(attempt.id, media_gateway)
            return "submit", False
        except SubmissionOutcomeUnknown:
            return "reconcile_submission", False

    complete = _checkpoint(attempt, "mediaconvert_complete")
    if complete is None:
        if attempt.next_poll_at is not None and attempt.next_poll_at > now:
            return "wait", False
        decision = poll_attempt(attempt.id, media_gateway, now)
        return "poll", decision.terminal

    outputs = _checkpoint(attempt, "outputs_verified")
    if outputs is None:
        snapshot = media_gateway.get_job(attempt.mediaconvert_job_id)
        verified = verify_mediaconvert_outputs(attempt.id, snapshot, storage_gateway)
        register_candidate(attempt.id, verified)
        attach_subtitle_assets(attempt.id)
        return "verify_outputs", False

    if not job.media or not job.media.active_asset_version_id:
        publish_candidate(attempt.id)
        return "publish", False
    if job.cleanup_status in {CleanupStatus.PENDING, CleanupStatus.FAILED}:
        cleanup_attempt(attempt.id, storage_gateway, now=now)
        return "cleanup", True
    return "done", True


def run_processing_tick(owner_token: str, now=None):
    now = now or timezone.now()
    acquisition = acquire_head_job(owner_token, _lease_seconds(), now=now)
    if acquisition is None:
        return TickResult("idle")
    attempt = _attempt_for_lease(acquisition)
    media_gateway = MediaConvertGateway()
    storage_gateway = ProcessingStorageGateway()
    action, terminal = _run_action(attempt, media_gateway, storage_gateway, now)
    if action == "done" or terminal:
        release_lease(owner_token)
        return TickResult(action, str(acquisition.job_id), str(acquisition.attempt_id), None, terminal or action == "done")
    heartbeat_lease(owner_token, _lease_seconds(), now=now)
    delay = _schedule_for(action)
    return TickResult(
        action,
        str(acquisition.job_id),
        str(acquisition.attempt_id),
        delay,
        terminal,
    )


aws_processing_tick = None


def reconcile_processing(now=None):
    now = now or timezone.now()
    task = aws_processing_tick
    if task is None:
        raise RuntimeError("AWS processing tick task is not configured.")
    job_ids = list(
        MediaIngestionJob.objects.filter(status=JobStatus.QUEUED, queued_at__lte=now)
        .order_by("queued_at", "id")
        .values_list("id", flat=True)[:20]
    )
    due_attempts = list(
        MediaJobAttempt.objects.filter(
            status=AttemptStatus.RUNNING,
        ).filter(
            Q(next_poll_at__isnull=True) | Q(next_poll_at__lte=now),
        )
        .order_by("next_poll_at", "id")
        .values_list("job_id", flat=True)[:20]
    )
    cleanup_jobs = list(
        MediaIngestionJob.objects.filter(
            cleanup_status__in=(CleanupStatus.PENDING, CleanupStatus.FAILED),
            status__in=(JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED),
        )
        .order_by("updated_at", "id")
        .values_list("id", flat=True)[:20]
    )
    unique = []
    seen = set()
    for job_id in [*job_ids, *due_attempts, *cleanup_jobs]:
        if job_id not in seen:
            unique.append(job_id)
            seen.add(job_id)
    for job_id in unique:
        task.apply_async(args=(str(job_id),), countdown=0)
    return ReconcileResult(len(unique))
