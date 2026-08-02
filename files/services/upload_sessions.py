from dataclasses import dataclass
from math import ceil
from pathlib import PurePosixPath
from uuid import UUID

from botocore.exceptions import ClientError
from django.db import models, transaction
from django.utils import timezone
from django.utils.html import strip_tags

from files.models import (
    AttemptArtifact,
    BrowserUploadObject,
    BrowserUploadPart,
    BrowserUploadSession,
    Media,
    MediaIngestionJob,
    MediaJobAttempt,
    MediaJobCheckpoint,
)
from files.models.domain import StorageBackend
from files.models.ingestion import (
    ArtifactPurpose,
    AttemptStatus,
    CheckpointStatus,
    JobStatus,
)
from files.models.uploads import (
    BrowserUploadObjectStatus,
    BrowserUploadStatus,
    BrowserUploadStrategy,
    PromotionStatus,
)
from files.services.processing_storage import ObjectEvidence
from files.services.hls_package import (
    MAX_HLS_FILES,
    MAX_HLS_TOTAL_SIZE,
    HlsInventoryEntry,
    UnsafeHlsPackage,
    validate_hls_inventory,
    validate_hls_manifests,
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
class CreateHlsSession:
    title: str
    total_size: int
    file_count: int
    package_fingerprint: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class FileSessionCreated:
    session_id: UUID
    job_id: UUID
    media_id: int
    object_id: UUID


@dataclass(frozen=True, slots=True)
class HlsSessionCreated:
    session_id: UUID
    job_id: UUID
    media_id: int


@dataclass(frozen=True, slots=True)
class RegisteredHlsObject:
    object_id: UUID
    relative_path: str
    strategy: str


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


def _validated_hls_command(command):
    title = strip_tags(command.title).strip() if isinstance(command.title, str) else ""
    if not title:
        raise InvalidUploadCommand("Title is required.")
    if not isinstance(command.total_size, int) or not 0 < command.total_size <= MAX_HLS_TOTAL_SIZE:
        raise InvalidUploadCommand("HLS package size must be between 1 byte and 20 GiB.")
    if not isinstance(command.file_count, int) or not 0 < command.file_count <= MAX_HLS_FILES:
        raise InvalidUploadCommand("HLS package must contain between 1 and 10,000 files.")
    if not isinstance(command.package_fingerprint, str) or not command.package_fingerprint:
        raise InvalidUploadCommand("Package fingerprint is required.")
    if not isinstance(command.idempotency_key, str) or not command.idempotency_key:
        raise InvalidUploadCommand("Idempotency key is required.")
    return title[:100]


def _hls_created_result(session):
    return HlsSessionCreated(session.id, session.job_id, session.job.media_id)


def create_hls_session(owner, command):
    title = _validated_hls_command(command)
    existing = BrowserUploadSession.objects.select_related("job__media").filter(
        create_idempotency_key=command.idempotency_key
    ).first()
    if existing is not None:
        matches = all(
            (
                existing.owner_id == owner.id,
                existing.source_kind == "hls",
                existing.expected_total_size == command.total_size,
                existing.expected_file_count == command.file_count,
                existing.file_fingerprint == command.package_fingerprint,
                existing.job.media_title_snapshot == title,
            )
        )
        if not matches:
            raise UploadIdempotencyConflict("Idempotency key was already used for another upload.")
        return _hls_created_result(existing)

    with transaction.atomic():
        media = Media.objects.create(
            title=title,
            user=owner,
            media_type="video",
            storage_backend=StorageBackend.AWS,
        )
        job = MediaIngestionJob.objects.create(
            media=media,
            media_title_snapshot=title,
            source_type="hls_zip",
            stage="waiting_upload",
        )
        session = BrowserUploadSession.objects.create(
            job=job,
            owner=owner,
            source_kind="hls",
            expected_total_size=command.total_size,
            expected_file_count=command.file_count,
            file_fingerprint=command.package_fingerprint,
            create_idempotency_key=command.idempotency_key,
        )
    return _hls_created_result(session)


def _registered_hls_objects(objects):
    return tuple(
        RegisteredHlsObject(upload_object.id, upload_object.relative_path, upload_object.strategy)
        for upload_object in objects
    )


def register_hls_inventory(session_id, owner_token, entries, gateway):
    require_upload_lease(session_id, owner_token)
    entries = tuple(entries)
    if not entries or len(entries) > 200:
        raise InvalidUploadCommand("An HLS inventory batch must contain between 1 and 200 files.")
    try:
        inventory = validate_hls_inventory(entries)
    except UnsafeHlsPackage as error:
        raise InvalidUploadCommand(str(error)) from error

    new_objects = []
    with transaction.atomic():
        require_upload_lease(session_id, owner_token)
        session = BrowserUploadSession.objects.select_for_update().get(pk=session_id)
        if session.source_kind != "hls" or session.status != BrowserUploadStatus.UPLOADING:
            raise InvalidUploadCommand("Only an active HLS upload can register inventory.")
        registered = []
        changed = False
        for entry in inventory.entries:
            strategy = (
                BrowserUploadStrategy.MULTIPART
                if entry.size >= session.part_size
                else BrowserUploadStrategy.SINGLE_PUT
            )
            upload_object, created = BrowserUploadObject.objects.get_or_create(
                session=session,
                relative_path=entry.path,
                defaults={
                    "s3_key": f"{session.upload_prefix}{entry.path}",
                    "strategy": strategy,
                    "expected_size": entry.size,
                    "compressed_size": entry.compressed_size,
                    "content_type": entry.content_type,
                    "expected_checksum": entry.checksum_sha256,
                },
            )
            expected = all(
                (
                    upload_object.expected_size == entry.size,
                    upload_object.compressed_size == entry.compressed_size,
                    upload_object.content_type == entry.content_type,
                    upload_object.expected_checksum == entry.checksum_sha256,
                    upload_object.strategy == strategy,
                )
            )
            if not created and not expected:
                raise UploadIdempotencyConflict("HLS path was already registered with different metadata.")
            if created:
                changed = True
                new_objects.append(upload_object)
            registered.append(upload_object)

        aggregate = BrowserUploadObject.objects.filter(session=session).aggregate(
            count=models.Count("id"),
            size=models.Sum("expected_size"),
        )
        if aggregate["count"] > session.expected_file_count or aggregate["size"] > session.expected_total_size:
            raise InvalidUploadCommand("Registered HLS inventory exceeds the declared package totals.")
        if changed:
            session.revision += 1
            session.save(update_fields=("revision", "updated_at"))

    for upload_object in new_objects:
        if upload_object.strategy == BrowserUploadStrategy.MULTIPART:
            upload_id = gateway.create_multipart(upload_object.s3_key, upload_object.content_type)
            BrowserUploadObject.objects.filter(pk=upload_object.pk).update(
                multipart_upload_id=upload_id,
                status=BrowserUploadObjectStatus.UPLOADING,
            )
            upload_object.multipart_upload_id = upload_id
            upload_object.status = BrowserUploadObjectStatus.UPLOADING
    return _registered_hls_objects(registered)


def issue_hls_object_url(session_id, owner_token, object_id, gateway):
    require_upload_lease(session_id, owner_token)
    upload_object = BrowserUploadObject.objects.get(pk=object_id, session_id=session_id)
    if upload_object.strategy != BrowserUploadStrategy.SINGLE_PUT:
        raise InvalidUploadCommand("Multipart HLS objects require Part URLs.")
    return gateway.presign_put(
        upload_object.s3_key,
        upload_object.content_type,
        upload_object.expected_size,
        upload_object.expected_checksum,
    )


def issue_part_urls(session_id, owner_token, part_requests, gateway, object_id=None):
    require_upload_lease(session_id, owner_token)
    requests = tuple(part_requests)
    if not requests or len(requests) > 20:
        raise InvalidUploadCommand("A Part URL request must contain between 1 and 20 Parts.")
    if len({request.part_number for request in requests}) != len(requests):
        raise InvalidUploadCommand("Part numbers must not be duplicated.")
    session = BrowserUploadSession.objects.get(pk=session_id)
    object_query = session.upload_objects
    upload_object = object_query.get(pk=object_id) if object_id is not None else object_query.get()
    if upload_object.strategy != BrowserUploadStrategy.MULTIPART:
        raise InvalidUploadCommand("Part URLs require a Multipart upload object.")
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


def _record_completed_upload(session_id, upload_object_id, evidence):
    with transaction.atomic():
        session = BrowserUploadSession.objects.select_for_update().select_related("job").get(pk=session_id)
        upload_object = BrowserUploadObject.objects.select_for_update().get(pk=upload_object_id)
        upload_object.status = BrowserUploadObjectStatus.VERIFIED
        upload_object.checksum = evidence.checksum_sha256
        upload_object.save(update_fields=("status", "checksum", "updated_at"))
        attempt, _ = MediaJobAttempt.objects.get_or_create(
            job=session.job,
            sequence=1,
            defaults={"status": AttemptStatus.QUEUED},
        )
        return attempt


def _promotion_destination(session, upload_object, attempt):
    suffix = PurePosixPath(upload_object.relative_path).suffix.lower()
    if not suffix:
        raise UploadVerificationFailed("Uploaded source extension is unavailable.")
    return f"originals/{session.job.media_id}/{attempt.id}/source{suffix}"


def _verify_promoted_object(upload_object, destination, evidence):
    if evidence.key != destination:
        raise UploadVerificationFailed("Promoted S3 object key does not match.")
    if evidence.size != upload_object.expected_size:
        raise UploadVerificationFailed("Promoted S3 object size does not match.")
    if evidence.content_type != upload_object.content_type:
        raise UploadVerificationFailed("Promoted S3 object content type does not match.")
    if not evidence.checksum_sha256:
        raise UploadVerificationFailed("Promoted S3 object checksum is unavailable.")


def _artifact_evidence(artifact):
    return ObjectEvidence(
        key=artifact.s3_key,
        size=artifact.size_bytes,
        content_type=artifact.content_type,
        checksum_sha256=artifact.checksum,
    )


def _is_missing_object(error):
    return str(error.response.get("Error", {}).get("Code", "")) in {
        "404",
        "NoSuchKey",
        "NotFound",
    }


def promote_file_original(session_id, gateway):
    should_copy = False
    with transaction.atomic():
        session = (
            BrowserUploadSession.objects.select_for_update()
            .select_related("job")
            .get(pk=session_id)
        )
        upload_object = BrowserUploadObject.objects.select_for_update().get(session=session)
        if upload_object.status != BrowserUploadObjectStatus.VERIFIED or not upload_object.checksum:
            raise UploadVerificationFailed("Uploaded source must be verified before promotion.")
        attempt, _ = MediaJobAttempt.objects.get_or_create(
            job=session.job,
            sequence=1,
            defaults={"status": AttemptStatus.QUEUED},
        )
        destination = _promotion_destination(session, upload_object, attempt)
        if upload_object.promoted_s3_key and upload_object.promoted_s3_key != destination:
            raise UploadVerificationFailed("Promotion destination conflicts with existing intent.")
        original_artifact, _ = AttemptArtifact.objects.get_or_create(
            attempt=attempt,
            s3_key=destination,
            defaults={
                "purpose": ArtifactPurpose.ORIGINAL,
                "size_bytes": upload_object.expected_size,
                "content_type": upload_object.content_type,
                "checksum": upload_object.checksum,
            },
        )
        AttemptArtifact.objects.get_or_create(
            attempt=attempt,
            s3_key=upload_object.s3_key,
            defaults={
                "purpose": ArtifactPurpose.UPLOAD_SOURCE,
                "size_bytes": upload_object.expected_size,
                "content_type": upload_object.content_type,
                "checksum": upload_object.checksum,
            },
        )
        if upload_object.promotion_status == PromotionStatus.VERIFIED:
            return _artifact_evidence(original_artifact)
        if upload_object.promotion_status in {PromotionStatus.PENDING, PromotionStatus.FAILED}:
            should_copy = True
        upload_object.promoted_s3_key = destination
        upload_object.promotion_status = PromotionStatus.COPYING
        upload_object.save(
            update_fields=("promoted_s3_key", "promotion_status", "updated_at")
        )

    if should_copy:
        gateway.copy_exact(upload_object.s3_key, destination)
        evidence = gateway.head_exact(destination)
    else:
        try:
            evidence = gateway.head_exact(destination)
        except ClientError as error:
            if not _is_missing_object(error):
                raise
            gateway.copy_exact(upload_object.s3_key, destination)
            evidence = gateway.head_exact(destination)
    try:
        _verify_promoted_object(upload_object, destination, evidence)
    except UploadVerificationFailed:
        BrowserUploadObject.objects.filter(pk=upload_object.pk).update(
            promotion_status=PromotionStatus.FAILED
        )
        raise

    with transaction.atomic():
        upload_object = BrowserUploadObject.objects.select_for_update().get(pk=upload_object.pk)
        if upload_object.promoted_s3_key != destination:
            raise UploadVerificationFailed("Promotion destination changed during verification.")
        AttemptArtifact.objects.filter(
            attempt=attempt,
            s3_key=destination,
        ).update(
            size_bytes=evidence.size,
            content_type=evidence.content_type,
            checksum=evidence.checksum_sha256,
            safe_error="",
        )
        upload_object.promotion_status = PromotionStatus.VERIFIED
        upload_object.save(update_fields=("promotion_status", "updated_at"))
    return evidence


def _finalize_file_completion(session_id, upload_object_id, evidence):
    with transaction.atomic():
        session = BrowserUploadSession.objects.select_for_update().select_related("job").get(pk=session_id)
        upload_object = BrowserUploadObject.objects.select_for_update().get(pk=upload_object_id)
        if session.status == BrowserUploadStatus.COMPLETED:
            return _progress_snapshot(session)
        if upload_object.promotion_status != PromotionStatus.VERIFIED:
            raise UploadVerificationFailed("Original promotion is not verified.")
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
        attempt = MediaJobAttempt.objects.get(job=session.job, sequence=1)
        MediaJobCheckpoint.objects.update_or_create(
            attempt=attempt,
            name="source_verified",
            defaults={
                "status": CheckpointStatus.COMPLETED,
                "evidence": {
                    "object_id": str(upload_object.id),
                    "s3_key": evidence.key,
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
    promotion_storage,
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
        if upload_object.status != BrowserUploadObjectStatus.VERIFIED:
            evidence = gateway.head_object(upload_object.s3_key)
            _verify_completed_object(upload_object, evidence)
            _record_completed_upload(session_id, upload_object.id, evidence)
        promoted = promote_file_original(session_id, promotion_storage)
        result = _finalize_file_completion(session_id, upload_object.id, promoted)
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
    _record_completed_upload(session_id, upload_object.id, evidence)
    promoted = promote_file_original(session_id, promotion_storage)
    result = _finalize_file_completion(session_id, upload_object.id, promoted)
    release_upload_lease(session_id, owner_token)
    return result


def _hls_inventory_from_objects(upload_objects):
    return validate_hls_inventory(
        HlsInventoryEntry(
            upload_object.relative_path,
            upload_object.expected_size,
            upload_object.compressed_size,
            upload_object.content_type,
            upload_object.expected_checksum,
        )
        for upload_object in upload_objects
    )


def complete_hls_upload(
    session_id,
    owner_token,
    idempotency_key,
    expected_revision,
    manifest_bodies,
    gateway,
):
    if not idempotency_key:
        raise InvalidUploadCommand("Completion idempotency key is required.")
    session = BrowserUploadSession.objects.select_related("job").get(pk=session_id)
    if session.status == BrowserUploadStatus.COMPLETED:
        if session.completion_idempotency_key != idempotency_key:
            raise UploadIdempotencyConflict("Upload was completed with another idempotency key.")
        return _progress_snapshot(session)
    require_upload_lease(session_id, owner_token)
    if session.source_kind != "hls":
        raise InvalidUploadCommand("Upload session is not an HLS package.")
    if session.revision != expected_revision:
        raise UploadRevisionConflict(session.revision)

    upload_objects = list(session.upload_objects.order_by("relative_path"))
    if len(upload_objects) != session.expected_file_count:
        raise UploadVerificationFailed("Registered HLS file count does not match the package declaration.")
    if sum(upload_object.expected_size for upload_object in upload_objects) != session.expected_total_size:
        raise UploadVerificationFailed("Registered HLS bytes do not match the package declaration.")
    try:
        inventory = _hls_inventory_from_objects(upload_objects)
        closure = validate_hls_manifests(inventory, manifest_bodies)
    except UnsafeHlsPackage as error:
        raise UploadVerificationFailed(str(error)) from error

    evidence_by_id = {}
    for upload_object in upload_objects:
        evidence = gateway.head_object(upload_object.s3_key)
        _verify_completed_object(upload_object, evidence)
        checksum_mismatch = (
            upload_object.strategy == BrowserUploadStrategy.SINGLE_PUT,
            evidence.checksum_sha256 != upload_object.expected_checksum,
        )
        if all(checksum_mismatch):
            raise UploadVerificationFailed("Completed S3 object checksum does not match.")
        evidence_by_id[upload_object.id] = evidence

    with transaction.atomic():
        locked = BrowserUploadSession.objects.select_for_update().select_related("job").get(pk=session_id)
        require_upload_lease(session_id, owner_token)
        if locked.revision != expected_revision:
            raise UploadRevisionConflict(locked.revision)
        locked_objects = list(
            BrowserUploadObject.objects.select_for_update().filter(session=locked).order_by("relative_path")
        )
        for upload_object in locked_objects:
            upload_object.status = BrowserUploadObjectStatus.VERIFIED
            upload_object.checksum = evidence_by_id[upload_object.id].checksum_sha256
        BrowserUploadObject.objects.bulk_update(locked_objects, ("status", "checksum", "updated_at"))
        locked.status = BrowserUploadStatus.COMPLETED
        locked.completion_idempotency_key = idempotency_key
        locked.confirmed_bytes = locked.expected_total_size
        locked.confirmed_file_count = locked.expected_file_count
        locked.revision += 1
        locked.save(
            update_fields=(
                "status",
                "completion_idempotency_key",
                "confirmed_bytes",
                "confirmed_file_count",
                "revision",
                "updated_at",
            )
        )
        entry_object = next(
            upload_object for upload_object in locked_objects if upload_object.relative_path == closure.entry_manifest
        )
        Media.objects.filter(pk=locked.job.media_id).update(hls_file=entry_object.s3_key)
        attempt, _ = MediaJobAttempt.objects.get_or_create(
            job=locked.job,
            sequence=1,
            defaults={"status": AttemptStatus.QUEUED},
        )
        MediaJobCheckpoint.objects.update_or_create(
            attempt=attempt,
            name="source_verified",
            defaults={
                "status": CheckpointStatus.COMPLETED,
                "evidence": {
                    "entry_manifest": closure.entry_manifest,
                    "closure_paths": list(closure.paths),
                    "object_ids": [str(upload_object.id) for upload_object in locked_objects],
                },
                "completed_at": timezone.now(),
            },
        )
        MediaIngestionJob.objects.filter(pk=locked.job_id).update(
            stage="source_verified",
            source_metadata={"hls_entry_manifest": closure.entry_manifest},
        )
        enqueue_job(locked.job_id)
        result = _progress_snapshot(locked)
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
