from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from files.models.uploads import (
    BrowserUploadLease,
    BrowserUploadSession,
    BrowserUploadStatus,
)


class UploadLeaseError(RuntimeError):
    pass


class UploadLeaseConflict(UploadLeaseError):
    pass


class UploadLeaseExpired(UploadLeaseError):
    pass


class UploadQueueBlocked(UploadLeaseError):
    def __init__(self, position):
        self.position = position
        super().__init__(f"Upload session is waiting at queue position {position}.")


@dataclass(frozen=True)
class UploadLeaseGrant:
    session_id: UUID
    job_id: UUID
    expires_at: object


def _validate_arguments(owner_token, lease_seconds):
    if not owner_token:
        raise ValueError("owner_token must not be empty")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")


def _lock_singleton():
    BrowserUploadLease.objects.get_or_create(singleton_key="default")
    return BrowserUploadLease.objects.select_for_update().get(singleton_key="default")


def _grant_from(lease):
    return UploadLeaseGrant(
        session_id=lease.session_id,
        job_id=lease.job_id,
        expires_at=lease.expires_at,
    )


def _is_live(lease, now):
    return bool(lease.session_id and lease.owner_token and lease.expires_at and lease.expires_at > now)


def _queue_position(session_id):
    waiting_ids = list(
        BrowserUploadSession.objects.filter(status=BrowserUploadStatus.WAITING)
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )
    try:
        return waiting_ids.index(session_id) + 1
    except ValueError as error:
        raise UploadLeaseConflict("Upload session is not waiting for a lease.") from error


@transaction.atomic
def acquire_upload_lease(session_id, owner_token, lease_seconds, now=None):
    _validate_arguments(owner_token, lease_seconds)
    current_time = now or timezone.now()
    session = BrowserUploadSession.objects.select_for_update().get(pk=session_id)
    lease = _lock_singleton()

    if _is_live(lease, current_time):
        if lease.session_id != session.id or lease.owner_token != owner_token:
            raise UploadLeaseConflict("Another browser owns the active upload lease.")
        lease.heartbeat_at = current_time
        lease.expires_at = current_time + timedelta(seconds=lease_seconds)
        lease.save(update_fields=("heartbeat_at", "expires_at", "updated_at"))
        return _grant_from(lease)

    position = _queue_position(session.id)
    if position != 1:
        raise UploadQueueBlocked(position)

    lease.session = session
    lease.job = session.job
    lease.owner_token = owner_token
    lease.heartbeat_at = current_time
    lease.expires_at = current_time + timedelta(seconds=lease_seconds)
    lease.save(
        update_fields=(
            "session",
            "job",
            "owner_token",
            "heartbeat_at",
            "expires_at",
            "updated_at",
        )
    )
    if session.status != BrowserUploadStatus.UPLOADING:
        session.status = BrowserUploadStatus.UPLOADING
        session.revision += 1
        session.save(update_fields=("status", "revision", "updated_at"))
    return _grant_from(lease)


@transaction.atomic
def heartbeat_upload_lease(session_id, owner_token, lease_seconds, now=None):
    _validate_arguments(owner_token, lease_seconds)
    current_time = now or timezone.now()
    lease = _lock_singleton()
    if lease.session_id != session_id or lease.owner_token != owner_token:
        raise UploadLeaseConflict("Upload lease owner does not match.")
    if not _is_live(lease, current_time):
        raise UploadLeaseExpired("Upload lease has expired.")
    lease.heartbeat_at = current_time
    lease.expires_at = current_time + timedelta(seconds=lease_seconds)
    lease.save(update_fields=("heartbeat_at", "expires_at", "updated_at"))
    return _grant_from(lease)


def require_upload_lease(session_id, owner_token, now=None):
    current_time = now or timezone.now()
    try:
        lease = BrowserUploadLease.objects.get(singleton_key="default")
    except BrowserUploadLease.DoesNotExist as error:
        raise UploadLeaseConflict("No upload lease is active.") from error
    if lease.session_id != session_id or lease.owner_token != owner_token:
        raise UploadLeaseConflict("Upload lease owner does not match.")
    if not _is_live(lease, current_time):
        raise UploadLeaseExpired("Upload lease has expired.")
    return lease


@transaction.atomic
def release_upload_lease(session_id, owner_token):
    if not owner_token:
        raise ValueError("owner_token must not be empty")
    lease = _lock_singleton()
    if lease.session_id != session_id or lease.owner_token != owner_token:
        raise UploadLeaseConflict("Upload lease owner does not match.")
    lease.session = None
    lease.job = None
    lease.owner_token = ""
    lease.heartbeat_at = None
    lease.expires_at = None
    lease.save(
        update_fields=(
            "session",
            "job",
            "owner_token",
            "heartbeat_at",
            "expires_at",
            "updated_at",
        )
    )
