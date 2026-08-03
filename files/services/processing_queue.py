from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from files.models import Media, MediaIngestionJob, MediaJobAttempt, ProcessingLease
from files.models.domain import MediaProcessingStatus, encoding_status_for
from files.models.ingestion import AttemptStatus, JobStatus


class QueueStateError(RuntimeError):
    """A job cannot perform the requested queue transition."""


class LeaseOwnershipError(RuntimeError):
    """The caller does not own the global processing lease."""


class LeaseReleaseError(RuntimeError):
    """The global lease cannot be released while its work is running."""


@dataclass(frozen=True, slots=True)
class LeaseAcquisition:
    job_id: UUID
    attempt_id: UUID
    expires_at: datetime


_TERMINAL_JOB_STATUSES = {
    JobStatus.FAILED,
    JobStatus.CANCELED,
    JobStatus.COMPLETED,
}
_TERMINAL_ATTEMPT_STATUSES = {
    AttemptStatus.FAILED,
    AttemptStatus.CANCELED,
    AttemptStatus.COMPLETED,
}


@transaction.atomic
def enqueue_job(job_id: UUID, *, now: datetime | None = None) -> MediaIngestionJob:
    queued_at = now or timezone.now()
    job = MediaIngestionJob.objects.select_for_update().get(pk=job_id)
    if job.status not in {JobStatus.QUEUED, JobStatus.FAILED, JobStatus.CANCELED}:
        raise QueueStateError(f"Cannot enqueue job from {job.status}")

    MediaIngestionJob.objects.filter(pk=job.pk).update(
        status=JobStatus.QUEUED,
        queued_at=queued_at,
        safe_error="",
        cancel_requested=False,
    )
    job.status = JobStatus.QUEUED
    job.queued_at = queued_at
    job.safe_error = ""
    job.cancel_requested = False
    if job.media_id is not None:
        Media.objects.filter(pk=job.media_id).exclude(
            processing_status=MediaProcessingStatus.READY
        ).update(
            processing_status=MediaProcessingStatus.QUEUED,
            encoding_status=encoding_status_for(MediaProcessingStatus.QUEUED),
        )
    return job


@transaction.atomic
def acquire_head_job(
    owner_token: str,
    lease_seconds: int,
    *,
    now: datetime | None = None,
) -> LeaseAcquisition | None:
    current_time = now or timezone.now()
    _validate_lease_arguments(owner_token, lease_seconds)
    ProcessingLease.objects.get_or_create(singleton_key="default")
    lease = ProcessingLease.objects.select_for_update().get(singleton_key="default")

    if lease.owner_token:
        if lease.expires_at is not None and lease.expires_at > current_time:
            if lease.owner_token != owner_token:
                return None
            return _extend_existing_lease(lease, owner_token, lease_seconds, current_time)
        if _lease_work_is_running(lease):
            return _transfer_expired_lease(lease, owner_token, lease_seconds, current_time)
        _clear_lease(lease)

    job = (
        MediaIngestionJob.objects.select_for_update()
        .queued()
        .filter(
            Q(source_type__in=("upload", "hls_zip"))
            | Q(source_type="youtube", source_metadata__import_requested=True)
        )
        .first()
    )
    if job is None:
        return None
    attempt = (
        MediaJobAttempt.objects.select_for_update()
        .filter(job=job, status=AttemptStatus.QUEUED)
        .order_by("-sequence")
        .first()
    )
    if attempt is None:
        latest_sequence = MediaJobAttempt.objects.filter(job=job).aggregate(value=Max("sequence"))["value"] or 0
        attempt = MediaJobAttempt.objects.create(
            job=job,
            sequence=latest_sequence + 1,
            status=AttemptStatus.QUEUED,
        )

    expires_at = current_time + timedelta(seconds=lease_seconds)
    MediaIngestionJob.objects.filter(pk=job.pk).update(status=JobStatus.RUNNING)
    MediaJobAttempt.objects.filter(pk=attempt.pk).update(
        status=AttemptStatus.RUNNING,
        started_at=attempt.started_at or current_time,
    )
    if job.media_id is not None:
        Media.objects.filter(
            pk=job.media_id,
            processing_status=MediaProcessingStatus.QUEUED,
        ).update(
            processing_status=MediaProcessingStatus.PROCESSING,
            encoding_status=encoding_status_for(MediaProcessingStatus.PROCESSING),
        )
    lease.job = job
    lease.attempt = attempt
    lease.owner_token = owner_token
    lease.heartbeat_at = current_time
    lease.expires_at = expires_at
    lease.save(
        update_fields=("job", "attempt", "owner_token", "heartbeat_at", "expires_at", "updated_at")
    )
    return LeaseAcquisition(job.id, attempt.id, expires_at)


@transaction.atomic
def heartbeat_lease(
    owner_token: str,
    lease_seconds: int,
    *,
    now: datetime | None = None,
) -> LeaseAcquisition:
    current_time = now or timezone.now()
    _validate_lease_arguments(owner_token, lease_seconds)
    lease = ProcessingLease.objects.select_for_update().get(singleton_key="default")
    if lease.owner_token != owner_token or lease.job_id is None or lease.attempt_id is None:
        raise LeaseOwnershipError("Processing lease belongs to another owner")
    return _extend_existing_lease(lease, owner_token, lease_seconds, current_time)


@transaction.atomic
def release_lease(owner_token: str) -> None:
    lease = ProcessingLease.objects.select_for_update().get(singleton_key="default")
    if lease.owner_token != owner_token:
        raise LeaseOwnershipError("Processing lease belongs to another owner")
    if _lease_work_is_running(lease):
        raise LeaseReleaseError("Running work must reach a terminal state before releasing its lease")
    _clear_lease(lease)


def _extend_existing_lease(
    lease: ProcessingLease,
    owner_token: str,
    lease_seconds: int,
    current_time: datetime,
) -> LeaseAcquisition:
    if lease.job_id is None or lease.attempt_id is None:
        raise LeaseOwnershipError("Processing lease has no bound work")
    expires_at = current_time + timedelta(seconds=lease_seconds)
    lease.owner_token = owner_token
    lease.heartbeat_at = current_time
    lease.expires_at = expires_at
    lease.save(update_fields=("owner_token", "heartbeat_at", "expires_at", "updated_at"))
    return LeaseAcquisition(lease.job_id, lease.attempt_id, expires_at)


def _transfer_expired_lease(
    lease: ProcessingLease,
    owner_token: str,
    lease_seconds: int,
    current_time: datetime,
) -> LeaseAcquisition:
    return _extend_existing_lease(lease, owner_token, lease_seconds, current_time)


def _lease_work_is_running(lease: ProcessingLease) -> bool:
    if lease.job_id is None or lease.attempt_id is None:
        return False
    job_status = MediaIngestionJob.objects.values_list("status", flat=True).get(pk=lease.job_id)
    attempt_status = MediaJobAttempt.objects.values_list("status", flat=True).get(
        pk=lease.attempt_id
    )
    return bool(
        job_status not in _TERMINAL_JOB_STATUSES
        and attempt_status not in _TERMINAL_ATTEMPT_STATUSES  # noqa: W503
    )


def _clear_lease(lease: ProcessingLease) -> None:
    lease.job = None
    lease.attempt = None
    lease.owner_token = ""
    lease.heartbeat_at = None
    lease.expires_at = None
    lease.save(update_fields=("job", "attempt", "owner_token", "heartbeat_at", "expires_at", "updated_at"))


def _validate_lease_arguments(owner_token: str, lease_seconds: int) -> None:
    if not owner_token or len(owner_token) > 255:
        raise ValueError("Lease owner token is invalid")
    if lease_seconds < 1:
        raise ValueError("Lease duration must be positive")
