from datetime import timedelta
from threading import Barrier

import pytest
from django.db import close_old_connections
from django.utils import timezone

from files.models import BrowserUploadLease, BrowserUploadSession, MediaIngestionJob
from files.services.upload_lease import (
    UploadLeaseConflict,
    UploadLeaseExpired,
    UploadQueueBlocked,
    acquire_upload_lease,
    heartbeat_upload_lease,
    release_upload_lease,
    require_upload_lease,
)
from tests.users.factories import UserFactory


@pytest.fixture
def administrator(db):
    return UserFactory(is_staff=True, is_superuser=True)


def make_session(administrator, key, queued_at):
    job = MediaIngestionJob.objects.create(
        media_title_snapshot=key,
        source_type="upload",
        queued_at=queued_at,
    )
    session = BrowserUploadSession.objects.create(
        job=job,
        owner=administrator,
        source_kind="file",
        expected_total_size=10,
        expected_file_count=1,
        create_idempotency_key=key,
    )
    BrowserUploadSession.objects.filter(pk=session.pk).update(created_at=queued_at)
    session.refresh_from_db()
    return session


@pytest.fixture
def queued_sessions(administrator):
    now = timezone.now()
    return (
        make_session(administrator, "first", now - timedelta(seconds=2)),
        make_session(administrator, "second", now - timedelta(seconds=1)),
    )


@pytest.mark.django_db
def test_only_fifo_head_can_acquire(queued_sessions):
    first, second = queued_sessions

    with pytest.raises(UploadQueueBlocked) as blocked:
        acquire_upload_lease(second.id, "browser-b", 60)

    assert blocked.value.position == 2
    grant = acquire_upload_lease(first.id, "browser-a", 60)
    assert grant.session_id == first.id
    assert grant.job_id == first.job_id
    first.refresh_from_db()
    assert first.status == "uploading"


@pytest.mark.django_db
def test_same_session_and_owner_can_idempotently_renew(queued_sessions):
    first, _ = queued_sessions
    now = timezone.now()
    initial = acquire_upload_lease(first.id, "browser-a", 60, now=now)
    renewed = acquire_upload_lease(
        first.id,
        "browser-a",
        60,
        now=now + timedelta(seconds=10),
    )

    assert renewed.expires_at > initial.expires_at


@pytest.mark.django_db
def test_live_lease_rejects_a_different_browser(queued_sessions):
    first, second = queued_sessions
    acquire_upload_lease(first.id, "browser-a", 60)

    with pytest.raises(UploadLeaseConflict):
        acquire_upload_lease(second.id, "browser-b", 60)


@pytest.mark.django_db
def test_expired_lease_allows_waiting_head_to_take_over(queued_sessions):
    first, second = queued_sessions
    now = timezone.now()
    acquire_upload_lease(first.id, "browser-a", 1, now=now)
    first.status = "completed"
    first.save(update_fields=("status", "updated_at"))

    grant = acquire_upload_lease(
        second.id,
        "browser-b",
        60,
        now=now + timedelta(seconds=2),
    )

    assert grant.session_id == second.id


@pytest.mark.django_db
def test_heartbeat_requires_exact_owner_and_live_lease(queued_sessions):
    first, _ = queued_sessions
    now = timezone.now()
    acquire_upload_lease(first.id, "browser-a", 10, now=now)

    with pytest.raises(UploadLeaseConflict):
        heartbeat_upload_lease(first.id, "browser-b", 10, now=now)

    with pytest.raises(UploadLeaseExpired):
        heartbeat_upload_lease(
            first.id,
            "browser-a",
            10,
            now=now + timedelta(seconds=11),
        )


@pytest.mark.django_db
def test_require_and_release_use_exact_owner(queued_sessions):
    first, _ = queued_sessions
    acquire_upload_lease(first.id, "browser-a", 60)

    assert require_upload_lease(first.id, "browser-a").session_id == first.id
    with pytest.raises(UploadLeaseConflict):
        release_upload_lease(first.id, "browser-b")

    release_upload_lease(first.id, "browser-a")
    lease = BrowserUploadLease.objects.get(pk="default")
    assert lease.session_id is None
    assert lease.job_id is None
    assert lease.owner_token == ""


@pytest.mark.django_db
@pytest.mark.parametrize("owner_token,lease_seconds", [("", 60), ("token", 0)])
def test_acquire_rejects_invalid_arguments(queued_sessions, owner_token, lease_seconds):
    first, _ = queued_sessions
    with pytest.raises(ValueError):
        acquire_upload_lease(first.id, owner_token, lease_seconds)


@pytest.mark.django_db(transaction=True)
def test_concurrent_first_acquisition_has_exactly_one_winner(administrator):
    from concurrent.futures import ThreadPoolExecutor

    now = timezone.now()
    session = make_session(administrator, "concurrent", now)
    BrowserUploadLease.objects.all().delete()
    barrier = Barrier(2)

    def attempt(token):
        close_old_connections()
        barrier.wait()
        try:
            acquire_upload_lease(session.id, token, 60)
            return "granted"
        except UploadLeaseConflict:
            return "conflict"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ("browser-a", "browser-b")))

    assert sorted(results) == ["conflict", "granted"]
    assert BrowserUploadLease.objects.filter(singleton_key="default").count() == 1
