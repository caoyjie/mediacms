from dataclasses import dataclass
from math import ceil
from pathlib import PurePosixPath
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags

from files.models import (
    BrowserUploadObject,
    BrowserUploadPart,
    BrowserUploadSession,
    Media,
    MediaIngestionJob,
    MediaJobAttempt,
    MediaJobCheckpoint,
)
from files.models.domain import StorageBackend
from files.models.ingestion import AttemptStatus, CheckpointStatus, JobStatus
from files.models.uploads import (
    BrowserUploadObjectStatus,
    BrowserUploadStatus,
    BrowserUploadStrategy,
)
from files.services.processing_queue import enqueue_job
from files.services.upload_lease import release_upload_lease, require_upload_lease


class InvalidUploadCommand(ValueError):
    pass


class UploadIdempotencyConflict(RuntimeError):
    pass


class UploadRevisionConflict(RuntimeError):
    def __init__(self, current_revision):
        self.current_revision = current_revision
        super().__init__("Upload session revision does not match.")


class UploadVerificationFailed(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CreateFileSession:
    title: str
    media_type: str
    filename: str
    size: int
    content_type: str
    fingerprint: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class FileSessionCreated:
    session_id: UUID
    job_id: UUID
    media_id: int
    object_id: UUID


@dataclass(frozen=True, slots=True)
class PartUploadRequest:
    part_number: int
    checksum_sha256: str


@dataclass(frozen=True, slots=True)
class UploadProgressSnapshot:
    session_id: UUID
    status: str
    confirmed_bytes: int
    confirmed_file_count: int
    expected_total_size: int
    expected_file_count: int
    revision: int
    file_fingerprint: str
    confirmed_parts: tuple[tuple[int, str, int, str], ...]


_FILE_EXTENSIONS = {
    "video": {".mp4", ".mov", ".mkv", ".webm", ".m4v"},
    "audio": {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"},
}


def _validated_file_command(command):
    title = strip_tags(command.title).strip() if isinstance(command.title, str) else ""
    if not title:
        raise InvalidUploadCommand("Title is required.")
    if command.media_type not in _FILE_EXTENSIONS:
        raise InvalidUploadCommand("Media type must be video or audio.")
    if not isinstance(command.filename, str) or not command.filename:
        raise InvalidUploadCommand("Filename is required.")
    if "/" in command.filename or "\\" in command.filename or "\x00" in command.filename:
        raise InvalidUploadCommand("Filename must not contain a path.")
    suffix = PurePosixPath(command.filename).suffix.lower()
    if suffix not in _FILE_EXTENSIONS[command.media_type]:
        raise InvalidUploadCommand("File extension is not supported.")
    if not isinstance(command.content_type, str) or not command.content_type.startswith(f"{command.media_type}/"):
        raise InvalidUploadCommand("Content type does not match the media type.")
    if not isinstance(command.size, int) or command.size <= 0:
        raise InvalidUploadCommand("File size must be positive.")
    if not isinstance(command.fingerprint, str) or not command.fingerprint:
        raise InvalidUploadCommand("File fingerprint is required.")
    if not isinstance(command.idempotency_key, str) or not command.idempotency_key:
        raise InvalidUploadCommand("Idempotency key is required.")
    return title[:100], suffix


def _created_result(session, upload_object):
    return FileSessionCreated(
        session_id=session.id,
        job_id=session.job_id,
        media_id=session.job.media_id,
        object_id=upload_object.id,
    )


def _existing_file_session(owner, command, title, suffix):
    try:
        session = (
            BrowserUploadSession.objects.select_related("job__media")
            .prefetch_related("upload_objects")
            .get(create_idempotency_key=command.idempotency_key)
        )
    except BrowserUploadSession.DoesNotExist:
        return None
    upload_objects = list(session.upload_objects.all())
    matches = all(
        (
            session.owner_id == owner.id,
            session.source_kind == "file",
            session.expected_total_size == command.size,
            session.expected_file_count == 1,
            session.file_fingerprint == command.fingerprint,
            session.job.media_title_snapshot == title,
            session.job.media.media_type == command.media_type,
            len(upload_objects) == 1,
            upload_objects[0].relative_path == f"source{suffix}",
            upload_objects[0].expected_size == command.size,
            upload_objects[0].content_type == command.content_type,
        )
    )
    if not matches:
        raise UploadIdempotencyConflict("Idempotency key was already used for another upload.")
    return _created_result(session, upload_objects[0])


def create_file_session(owner, command, gateway):
    title, suffix = _validated_file_command(command)
    existing = _existing_file_session(owner, command, title, suffix)
    if existing is not None:
        return existing

    with transaction.atomic():
        job = MediaIngestionJob(
            media_title_snapshot=title,
            source_type="upload",
            stage="waiting_upload",
        )
        session = BrowserUploadSession(
            job=job,
            owner=owner,
            source_kind="file",
            expected_total_size=command.size,
            expected_file_count=1,
            file_fingerprint=command.fingerprint,
            create_idempotency_key=command.idempotency_key,
        )
        relative_path = f"source{suffix}"
        s3_key = f"{session.upload_prefix}{relative_path}"
        media = Media.objects.create(
            title=title,
            user=owner,
            media_type=command.media_type,
            media_file=s3_key,
            storage_backend=StorageBackend.AWS,
        )
        job.media = media
        job.save()
        session.save()
        upload_object = BrowserUploadObject.objects.create(
            session=session,
            relative_path=relative_path,
            s3_key=s3_key,
            strategy=BrowserUploadStrategy.MULTIPART,
            expected_size=command.size,
            content_type=command.content_type,
        )

    try:
        upload_id = gateway.create_multipart(s3_key, command.content_type)
    except Exception:
        BrowserUploadSession.objects.filter(pk=session.pk).update(
            status="failed",
            safe_error="Unable to initialize private storage upload.",
        )
        raise
    BrowserUploadObject.objects.filter(pk=upload_object.pk).update(
        multipart_upload_id=upload_id,
        status=BrowserUploadObjectStatus.UPLOADING,
    )
    upload_object.multipart_upload_id = upload_id
    upload_object.status = BrowserUploadObjectStatus.UPLOADING
    return _created_result(session, upload_object)


def issue_part_urls(session_id, owner_token, part_requests, gateway):
    require_upload_lease(session_id, owner_token)
    requests = tuple(part_requests)
    if not requests or len(requests) > 20:
        raise InvalidUploadCommand("A Part URL request must contain between 1 and 20 Parts.")
    if len({request.part_number for request in requests}) != len(requests):
        raise InvalidUploadCommand("Part numbers must not be duplicated.")
    session = BrowserUploadSession.objects.get(pk=session_id)
    upload_object = session.upload_objects.get()
    maximum_part = ceil(upload_object.expected_size / session.part_size)
    if any(request.part_number < 1 or request.part_number > maximum_part for request in requests):
        raise InvalidUploadCommand("Part number is outside the expected file range.")
    return tuple(
        gateway.presign_part(
            upload_object.s3_key,
            upload_object.multipart_upload_id,
            request.part_number,
            request.checksum_sha256,
        )
        for request in requests
    )


def _progress_snapshot(session):
    confirmed_parts = tuple(
        BrowserUploadPart.objects.filter(upload_object__session=session)
        .order_by("upload_object_id", "part_number")
        .values_list("part_number", "etag", "size", "checksum_sha256")
    )
    return UploadProgressSnapshot(
        session_id=session.id,
        status=session.status,
        confirmed_bytes=session.confirmed_bytes,
        confirmed_file_count=session.confirmed_file_count,
        expected_total_size=session.expected_total_size,
        expected_file_count=session.expected_file_count,
        revision=session.revision,
        file_fingerprint=session.file_fingerprint,
        confirmed_parts=confirmed_parts,
    )


def _validate_reconciled_parts(session, upload_object, listed_parts):
    maximum_part = ceil(upload_object.expected_size / session.part_size)
    numbers = [part.part_number for part in listed_parts]
    if len(numbers) != len(set(numbers)) or any(number > maximum_part for number in numbers):
        raise InvalidUploadCommand("S3 returned a Part outside the expected file range.")
    if any(part.size > session.part_size for part in listed_parts):
        raise InvalidUploadCommand("S3 returned a Part larger than the configured Part size.")
    confirmed_bytes = sum(part.size for part in listed_parts)
    if confirmed_bytes > upload_object.expected_size:
        raise InvalidUploadCommand("S3 Part bytes exceed the expected file size.")
    return confirmed_bytes


def _replace_authoritative_parts(session, upload_object, listed_parts):
    confirmed_bytes = _validate_reconciled_parts(session, upload_object, listed_parts)
    BrowserUploadPart.objects.filter(upload_object=upload_object).delete()
    BrowserUploadPart.objects.bulk_create(
        [
            BrowserUploadPart(
                upload_object=upload_object,
                part_number=part.part_number,
                etag=part.etag,
                size=part.size,
                checksum_sha256=part.checksum_sha256,
            )
            for part in listed_parts
        ]
    )
    session.confirmed_bytes = confirmed_bytes
    session.revision += 1
    session.save(update_fields=("confirmed_bytes", "revision", "updated_at"))
    return _progress_snapshot(session)


def reconcile_parts(session_id, owner_token, gateway):
    require_upload_lease(session_id, owner_token)
    upload_object = BrowserUploadObject.objects.get(session_id=session_id)
    listed_parts = gateway.list_parts(
        upload_object.s3_key,
        upload_object.multipart_upload_id,
    )

    with transaction.atomic():
        require_upload_lease(session_id, owner_token)
        session = BrowserUploadSession.objects.select_for_update().get(pk=session_id)
        upload_object = BrowserUploadObject.objects.select_for_update().get(pk=upload_object.pk)
        return _replace_authoritative_parts(session, upload_object, listed_parts)


def get_resume_snapshot(session_id, gateway):
    session = BrowserUploadSession.objects.get(pk=session_id)
    upload_object = BrowserUploadObject.objects.get(session=session)
    if session.status in {BrowserUploadStatus.COMPLETED, BrowserUploadStatus.CANCELED}:
        return _progress_snapshot(session)
    listed_parts = gateway.list_parts(upload_object.s3_key, upload_object.multipart_upload_id)
    with transaction.atomic():
        session = BrowserUploadSession.objects.select_for_update().get(pk=session_id)
        upload_object = BrowserUploadObject.objects.select_for_update().get(pk=upload_object.pk)
        return _replace_authoritative_parts(session, upload_object, listed_parts)


def _validated_completion_parts(session, upload_object, listed_parts):
    if not listed_parts:
        raise UploadVerificationFailed("S3 has no uploaded Parts.")
    expected_numbers = list(range(1, len(listed_parts) + 1))
    if [part.part_number for part in listed_parts] != expected_numbers:
        raise UploadVerificationFailed("S3 Parts are not contiguous.")
    for part in listed_parts[:-1]:
        if part.size < 5 * 1024 * 1024:
            raise UploadVerificationFailed("A non-final S3 Part is below the minimum size.")
        if part.size > session.part_size:
            raise UploadVerificationFailed("An S3 Part exceeds the configured Part size.")
    total_size = sum(part.size for part in listed_parts)
    if total_size != upload_object.expected_size:
        raise UploadVerificationFailed("S3 Part size does not match the expected file size.")
    return total_size


def _verify_completed_object(upload_object, evidence):
    if evidence.size != upload_object.expected_size:
        raise UploadVerificationFailed("Completed S3 object size does not match.")
    if evidence.content_type != upload_object.content_type:
        raise UploadVerificationFailed("Completed S3 object content type does not match.")
    if not evidence.checksum_sha256:
        raise UploadVerificationFailed("Completed S3 object checksum is unavailable.")


def _finalize_file_completion(session_id, upload_object_id, evidence):
    with transaction.atomic():
        session = BrowserUploadSession.objects.select_for_update().select_related("job").get(pk=session_id)
        upload_object = BrowserUploadObject.objects.select_for_update().get(pk=upload_object_id)
        if session.status == BrowserUploadStatus.COMPLETED:
            return _progress_snapshot(session)
        upload_object.status = BrowserUploadObjectStatus.VERIFIED
        upload_object.checksum = evidence.checksum_sha256
        upload_object.save(update_fields=("status", "checksum", "updated_at"))
        session.status = BrowserUploadStatus.COMPLETED
        session.confirmed_bytes = session.expected_total_size
        session.confirmed_file_count = 1
        session.revision += 1
        session.save(
            update_fields=(
                "status",
                "confirmed_bytes",
                "confirmed_file_count",
                "revision",
                "updated_at",
            )
        )
        attempt, _ = MediaJobAttempt.objects.get_or_create(
            job=session.job,
            sequence=1,
            defaults={"status": AttemptStatus.QUEUED},
        )
        MediaJobCheckpoint.objects.update_or_create(
            attempt=attempt,
            name="source_verified",
            defaults={
                "status": CheckpointStatus.COMPLETED,
                "evidence": {
                    "object_id": str(upload_object.id),
                    "size": evidence.size,
                    "content_type": evidence.content_type,
                    "checksum_sha256": evidence.checksum_sha256,
                },
                "completed_at": timezone.now(),
            },
        )
        MediaIngestionJob.objects.filter(pk=session.job_id).update(stage="source_verified")
        enqueue_job(session.job_id)
        return _progress_snapshot(session)


def complete_file_upload(
    session_id,
    owner_token,
    idempotency_key,
    expected_revision,
    gateway,
):
    if not idempotency_key:
        raise InvalidUploadCommand("Completion idempotency key is required.")
    session = BrowserUploadSession.objects.get(pk=session_id)
    if session.status == BrowserUploadStatus.COMPLETED:
        if session.completion_idempotency_key != idempotency_key:
            raise UploadIdempotencyConflict("Upload was completed with another idempotency key.")
        return _progress_snapshot(session)
    require_upload_lease(session_id, owner_token)
    if session.revision != expected_revision:
        raise UploadRevisionConflict(session.revision)
    upload_object = BrowserUploadObject.objects.get(session=session)
    if session.status == BrowserUploadStatus.VERIFYING:
        if session.completion_idempotency_key != idempotency_key:
            raise UploadIdempotencyConflict("Upload verification belongs to another idempotency key.")
        evidence = gateway.head_object(upload_object.s3_key)
        _verify_completed_object(upload_object, evidence)
        result = _finalize_file_completion(session_id, upload_object.id, evidence)
        release_upload_lease(session_id, owner_token)
        return result
    listed_parts = gateway.list_parts(upload_object.s3_key, upload_object.multipart_upload_id)
    _validated_completion_parts(session, upload_object, listed_parts)

    with transaction.atomic():
        locked = BrowserUploadSession.objects.select_for_update().get(pk=session_id)
        if locked.revision != expected_revision:
            raise UploadRevisionConflict(locked.revision)
        require_upload_lease(session_id, owner_token)
        locked.status = BrowserUploadStatus.VERIFYING
        locked.completion_idempotency_key = idempotency_key
        locked.revision += 1
        locked.save(
            update_fields=(
                "status",
                "completion_idempotency_key",
                "revision",
                "updated_at",
            )
        )

    gateway.complete_multipart(
        upload_object.s3_key,
        upload_object.multipart_upload_id,
        listed_parts,
    )
    evidence = gateway.head_object(upload_object.s3_key)
    _verify_completed_object(upload_object, evidence)
    result = _finalize_file_completion(session_id, upload_object.id, evidence)
    release_upload_lease(session_id, owner_token)
    return result


def cancel_upload(session_id, owner_token, gateway):
    session = BrowserUploadSession.objects.get(pk=session_id)
    if session.status == BrowserUploadStatus.CANCELED:
        return _progress_snapshot(session)
    require_upload_lease(session_id, owner_token)
    upload_objects = list(session.upload_objects.all())
    small_object_keys = []
    for upload_object in upload_objects:
        if upload_object.strategy == BrowserUploadStrategy.MULTIPART and upload_object.multipart_upload_id:
            gateway.abort_multipart(upload_object.s3_key, upload_object.multipart_upload_id)
        elif upload_object.status in {
            BrowserUploadObjectStatus.UPLOADED,
            BrowserUploadObjectStatus.VERIFIED,
        }:
            small_object_keys.append(upload_object.s3_key)
    if small_object_keys:
        gateway.delete_exact_keys(tuple(small_object_keys))

    with transaction.atomic():
        session = BrowserUploadSession.objects.select_for_update().get(pk=session_id)
        BrowserUploadObject.objects.filter(session=session).update(
            status=BrowserUploadObjectStatus.ABORTED,
        )
        session.status = BrowserUploadStatus.CANCELED
        session.revision += 1
        session.save(update_fields=("status", "revision", "updated_at"))
        MediaIngestionJob.objects.filter(pk=session.job_id).update(
            status=JobStatus.CANCELED,
            cancel_requested=True,
        )
        result = _progress_snapshot(session)
    release_upload_lease(session_id, owner_token)
    return result


def pause_upload(session_id, owner_token):
    require_upload_lease(session_id, owner_token)
    with transaction.atomic():
        session = BrowserUploadSession.objects.select_for_update().get(pk=session_id)
        if session.status != BrowserUploadStatus.UPLOADING:
            raise InvalidUploadCommand("Only an active upload can be paused.")
        session.status = BrowserUploadStatus.PAUSED
        session.revision += 1
        session.save(update_fields=("status", "revision", "updated_at"))
        result = _progress_snapshot(session)
    release_upload_lease(session_id, owner_token)
    return result


@transaction.atomic
def resume_upload(session_id):
    session = BrowserUploadSession.objects.select_for_update().get(pk=session_id)
    if session.status == BrowserUploadStatus.WAITING:
        return _progress_snapshot(session)
    if session.status != BrowserUploadStatus.PAUSED:
        raise InvalidUploadCommand("Only a paused upload can return to the queue.")
    session.status = BrowserUploadStatus.WAITING
    session.revision += 1
    session.save(update_fields=("status", "revision", "updated_at"))
    return _progress_snapshot(session)
