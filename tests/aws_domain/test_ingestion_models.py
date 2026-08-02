from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from files.models import (
    Media,
    MediaAssetVersion,
    MediaIngestionJob,
    MediaJobAttempt,
    MediaJobCheckpoint,
    ProcessingLease,
)
from tests.users.factories import UserFactory


def create_aws_media(title: str) -> Media:
    media = Media(
        title=title,
        user=UserFactory(),
        storage_backend="aws",
        media_file="aws-source/pending-upload",
    )
    Media.objects.bulk_create([media])
    return media


@pytest.mark.django_db
def test_attempt_sequence_is_unique_per_job():
    media = create_aws_media("Job test")
    job = MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot=media.title,
        source_type="upload",
    )
    MediaJobAttempt.objects.create(job=job, sequence=1, status="queued")

    with pytest.raises(IntegrityError), transaction.atomic():
        MediaJobAttempt.objects.create(job=job, sequence=1, status="queued")


@pytest.mark.django_db
def test_processing_lease_accepts_only_the_default_singleton_key():
    ProcessingLease.objects.create()

    with pytest.raises(IntegrityError), transaction.atomic():
        ProcessingLease.objects.create(singleton_key="other")


@pytest.mark.django_db
def test_queued_jobs_are_ordered_by_queued_time_then_id():
    media = create_aws_media("Queue test")
    queued_at = timezone.now()
    later = MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot=media.title,
        source_type="upload",
        queued_at=queued_at + timedelta(seconds=1),
    )
    first = MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot=media.title,
        source_type="youtube",
        queued_at=queued_at,
    )

    assert list(MediaIngestionJob.objects.queued().values_list("id", flat=True)) == [first.id, later.id]


@pytest.mark.django_db
def test_checkpoint_name_is_unique_per_attempt_and_version_links_once():
    media = create_aws_media("Checkpoint test")
    job = MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot=media.title,
        source_type="hls_zip",
    )
    attempt = MediaJobAttempt.objects.create(job=job, sequence=1, status="running")
    MediaJobCheckpoint.objects.create(
        attempt=attempt,
        name="source_verified",
        status="completed",
        input_fingerprint="sha256:source",
        evidence={"verified": True},
    )
    version = MediaAssetVersion.objects.create(
        media=media,
        attempt=attempt,
        manifest_key="candidates/checkpoint/master.m3u8",
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        MediaJobCheckpoint.objects.create(
            attempt=attempt,
            name="source_verified",
            status="completed",
        )

    assert attempt.asset_version == version


@pytest.mark.django_db
def test_job_audit_survives_media_row_deletion():
    media = create_aws_media("Deleted title")
    job = MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot=media.title,
        source_type="upload",
    )

    media.delete()

    job.refresh_from_db()
    assert job.media_id is None
    assert job.media_title_snapshot == "Deleted title"
