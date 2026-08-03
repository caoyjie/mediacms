from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.db import close_old_connections
from django.utils import timezone

from files.models import Media, MediaIngestionJob, MediaJobAttempt, ProcessingLease
from files.services.processing_queue import (
    LeaseOwnershipError,
    LeaseReleaseError,
    acquire_head_job,
    enqueue_job,
    heartbeat_lease,
    release_lease,
)
from tests.users.factories import UserFactory


def create_aws_media(title: str) -> Media:
    media = Media(
        title=title,
        user=UserFactory(username=f"queue-{uuid4().hex}"),
        friendly_token=f"queue-{uuid4().hex}",
        storage_backend="aws",
        media_file="aws-source/pending-upload",
    )
    Media.objects.bulk_create([media])
    return media


def create_job(title: str, *, queued_at=None, job_id=None, status="queued") -> MediaIngestionJob:
    media = create_aws_media(title)
    return MediaIngestionJob.objects.create(
        id=job_id or uuid4(),
        media=media,
        media_title_snapshot=media.title,
        source_type="upload",
        status=status,
        queued_at=queued_at or timezone.now(),
    )


@pytest.mark.django_db(transaction=True)
def test_only_oldest_queued_job_is_acquired():
    now = timezone.now()
    first = create_job("First", queued_at=now)
    create_job("Second", queued_at=now + timedelta(seconds=1))

    acquired = acquire_head_job("worker-a", lease_seconds=60, now=now)

    assert acquired.job_id == first.id
    assert MediaJobAttempt.objects.get(pk=acquired.attempt_id).sequence == 1
    first.refresh_from_db()
    assert first.status == "running"


@pytest.mark.django_db(transaction=True)
def test_empty_queue_returns_no_acquisition():
    assert acquire_head_job("worker-a", lease_seconds=60) is None


@pytest.mark.django_db(transaction=True)
def test_metadata_only_youtube_job_does_not_enter_import_queue():
    media = create_aws_media("Metadata only")
    MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot=media.title,
        source_type="youtube",
        status="queued",
        source_metadata={"url": "https://www.youtube.com/watch?v=abc123"},
    )

    assert acquire_head_job("worker-a", lease_seconds=60) is None


@pytest.mark.django_db(transaction=True)
def test_live_lease_blocks_second_owner():
    now = timezone.now()
    create_job("First", queued_at=now)
    create_job("Second", queued_at=now + timedelta(seconds=1))

    assert acquire_head_job("worker-a", lease_seconds=60, now=now) is not None
    assert acquire_head_job("worker-b", lease_seconds=60, now=now) is None


@pytest.mark.django_db(transaction=True)
def test_same_owner_reacquires_live_lease_idempotently():
    now = timezone.now()
    create_job("First", queued_at=now)
    first = acquire_head_job("worker-a", lease_seconds=60, now=now)

    repeated = acquire_head_job("worker-a", lease_seconds=60, now=now + timedelta(seconds=1))

    assert repeated.job_id == first.job_id
    assert repeated.attempt_id == first.attempt_id
    assert MediaJobAttempt.objects.filter(job_id=first.job_id).count() == 1


@pytest.mark.django_db(transaction=True)
def test_expired_lease_takeover_reuses_existing_attempt():
    now = timezone.now()
    create_job("First", queued_at=now)
    first = acquire_head_job("dead-worker", lease_seconds=1, now=now)

    recovered = acquire_head_job("reconciler", lease_seconds=60, now=now + timedelta(seconds=2))

    assert recovered.job_id == first.job_id
    assert recovered.attempt_id == first.attempt_id
    assert ProcessingLease.objects.get().owner_token == "reconciler"
    assert MediaJobAttempt.objects.filter(job_id=first.job_id).count() == 1


@pytest.mark.django_db(transaction=True)
def test_release_allows_next_fifo_job_to_start():
    now = timezone.now()
    first_job = create_job("First", queued_at=now)
    second_job = create_job("Second", queued_at=now + timedelta(seconds=1))
    first = acquire_head_job("worker-a", lease_seconds=60, now=now)
    MediaIngestionJob.objects.filter(pk=first.job_id).update(status="completed")
    MediaJobAttempt.objects.filter(pk=first.attempt_id).update(status="completed")

    release_lease("worker-a")
    second = acquire_head_job("worker-b", lease_seconds=60, now=now + timedelta(seconds=2))

    assert first.job_id == first_job.id
    assert second.job_id == second_job.id


@pytest.mark.django_db(transaction=True)
def test_running_attempt_cannot_release_lease_early():
    create_job("First")
    acquire_head_job("worker-a", lease_seconds=60)

    with pytest.raises(LeaseReleaseError):
        release_lease("worker-a")


@pytest.mark.django_db(transaction=True)
def test_wrong_owner_cannot_heartbeat_or_release():
    now = timezone.now()
    create_job("First", queued_at=now)
    acquire_head_job("worker-a", lease_seconds=60, now=now)

    with pytest.raises(LeaseOwnershipError):
        heartbeat_lease("worker-b", lease_seconds=60, now=now)
    with pytest.raises(LeaseOwnershipError):
        release_lease("worker-b")


@pytest.mark.django_db(transaction=True)
def test_resume_creates_next_attempt_sequence():
    job = create_job("Resume", status="failed")
    MediaJobAttempt.objects.create(job=job, sequence=1, status="failed")

    enqueue_job(job.id)
    acquired = acquire_head_job("worker-a", lease_seconds=60)

    assert acquired.job_id == job.id
    assert MediaJobAttempt.objects.get(pk=acquired.attempt_id).sequence == 2


@pytest.mark.django_db(transaction=True)
def test_equal_queue_time_uses_uuid_as_stable_tie_breaker():
    now = timezone.now()
    later_id = UUID("00000000-0000-0000-0000-000000000002")
    first_id = UUID("00000000-0000-0000-0000-000000000001")
    create_job("Later UUID", queued_at=now, job_id=later_id)
    create_job("First UUID", queued_at=now, job_id=first_id)

    acquired = acquire_head_job("worker-a", lease_seconds=60, now=now)

    assert acquired.job_id == first_id


@pytest.mark.django_db(transaction=True)
def test_two_database_connections_produce_exactly_one_winner():
    create_job("First")
    create_job("Second")

    def acquire(owner):
        close_old_connections()
        try:
            return acquire_head_job(owner, lease_seconds=60)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(acquire, ("worker-a", "worker-b")))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert MediaJobAttempt.objects.count() == 1
