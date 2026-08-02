import uuid

import pytest
from django.db import IntegrityError, transaction

from files.models import MediaIngestionJob
from files.models.uploads import (
    BrowserUploadLease,
    BrowserUploadObject,
    BrowserUploadPart,
    BrowserUploadSession,
)
from tests.users.factories import UserFactory


@pytest.fixture
def administrator(db):
    return UserFactory(is_staff=True, is_superuser=True)


@pytest.fixture
def ingestion_job(db):
    return MediaIngestionJob.objects.create(
        media_title_snapshot="Upload",
        source_type="upload",
    )


@pytest.fixture
def upload_session(administrator, ingestion_job):
    return BrowserUploadSession.objects.create(
        job=ingestion_job,
        owner=administrator,
        source_kind="file",
        expected_total_size=32_000_000,
        expected_file_count=1,
        file_fingerprint="sha256:test",
        create_idempotency_key="create-test",
    )


@pytest.fixture
def upload_object(upload_session):
    return BrowserUploadObject.objects.create(
        session=upload_session,
        relative_path="source.mp4",
        s3_key=f"{upload_session.upload_prefix}source.mp4",
        strategy="multipart",
        expected_size=32_000_000,
        content_type="video/mp4",
        multipart_upload_id="s3-upload-id",
    )


@pytest.mark.django_db
def test_session_uses_uuid_and_computes_server_owned_prefix(upload_session):
    assert isinstance(upload_session.id, uuid.UUID)
    assert upload_session.upload_prefix == (
        f"uploads/{upload_session.job_id}/{upload_session.id}/"
    )
    assert upload_session.status == "waiting"
    assert upload_session.part_size == 16 * 1024 * 1024
    assert upload_session.confirmed_bytes == 0
    assert upload_session.confirmed_file_count == 0
    assert upload_session.revision == 1


@pytest.mark.django_db
def test_create_idempotency_key_is_unique(administrator, ingestion_job, upload_session):
    with pytest.raises(IntegrityError), transaction.atomic():
        BrowserUploadSession.objects.create(
            job=ingestion_job,
            owner=administrator,
            source_kind="file",
            expected_total_size=1,
            expected_file_count=1,
            create_idempotency_key=upload_session.create_idempotency_key,
        )


@pytest.mark.django_db
def test_relative_path_is_unique_per_session(upload_session, upload_object):
    with pytest.raises(IntegrityError), transaction.atomic():
        BrowserUploadObject.objects.create(
            session=upload_session,
            relative_path=upload_object.relative_path,
            s3_key=f"{upload_session.upload_prefix}duplicate.mp4",
            strategy="multipart",
            expected_size=1,
            content_type="video/mp4",
        )


@pytest.mark.django_db
def test_part_number_is_unique_per_object(upload_object):
    BrowserUploadPart.objects.create(
        upload_object=upload_object,
        part_number=1,
        etag='"etag-a"',
        size=5 * 1024 * 1024,
        checksum_sha256="checksum-a",
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        BrowserUploadPart.objects.create(
            upload_object=upload_object,
            part_number=1,
            etag='"etag-b"',
            size=5 * 1024 * 1024,
            checksum_sha256="checksum-b",
        )


@pytest.mark.django_db
@pytest.mark.parametrize("part_number", [0, 10_001])
def test_part_number_must_be_in_s3_range(upload_object, part_number):
    with pytest.raises(IntegrityError), transaction.atomic():
        BrowserUploadPart.objects.create(
            upload_object=upload_object,
            part_number=part_number,
            etag='"etag"',
            size=1,
            checksum_sha256="checksum",
        )


@pytest.mark.django_db
def test_sizes_and_file_count_must_be_positive(administrator, ingestion_job):
    with pytest.raises(IntegrityError), transaction.atomic():
        BrowserUploadSession.objects.create(
            job=ingestion_job,
            owner=administrator,
            source_kind="hls",
            expected_total_size=0,
            expected_file_count=0,
            create_idempotency_key="invalid-size",
        )


@pytest.mark.django_db
def test_upload_lease_is_a_singleton(upload_session):
    BrowserUploadLease.objects.create(session=upload_session, job=upload_session.job)
    with pytest.raises(IntegrityError), transaction.atomic():
        BrowserUploadLease.objects.create(
            singleton_key="another",
            session=upload_session,
            job=upload_session.job,
        )
