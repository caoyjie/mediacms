from collections.abc import Collection, Mapping
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags

from files.models import Category, Media, MediaAsset, MediaAssetVersion, Tag
from files.models.domain import MediaProcessingStatus, encoding_status_for
from files.models.utils import MEDIA_STATES


class InvalidMediaTransition(ValueError):
    """The requested processing-state transition is not allowed."""


class InvalidAssetActivation(ValueError):
    """The requested asset version is not a complete candidate for this media."""


class InvalidMetadataUpdate(ValueError):
    """The requested metadata update contains unsupported values."""


class InvalidDeletionRequest(ValueError):
    """The media cannot enter deletion from its current state."""


class MediaRevisionConflict(RuntimeError):
    def __init__(self, current_revision: int, current_values: Mapping[str, Any]):
        super().__init__("Media metadata revision conflict")
        self.current_revision = current_revision
        self.current_values = dict(current_values)


_TRANSITIONS = {
    MediaProcessingStatus.DRAFT: {MediaProcessingStatus.QUEUED},
    MediaProcessingStatus.QUEUED: {MediaProcessingStatus.PROCESSING},
    MediaProcessingStatus.PROCESSING: {
        MediaProcessingStatus.READY,
        MediaProcessingStatus.FAILED,
    },
    MediaProcessingStatus.FAILED: {MediaProcessingStatus.QUEUED},
    MediaProcessingStatus.READY: set(),
}

_ALLOWED_METADATA_FIELDS = {"title", "description", "state", "category_ids", "tag_ids"}
_ALLOWED_METADATA_SOURCES = {"admin", "file_probe", "youtube", "default"}
_SCALAR_METADATA_FIELDS = {"title", "description", "state"}
_AUTOMATIC_SOURCES = _ALLOWED_METADATA_SOURCES - {"admin"}


@transaction.atomic
def transition_media(media_id: int, target: str) -> Media:
    media = Media.objects.select_for_update().get(pk=media_id)
    try:
        target_status = MediaProcessingStatus(target)
    except ValueError as exc:
        raise InvalidMediaTransition(f"Unknown processing status: {target}") from exc

    current_status = MediaProcessingStatus(media.processing_status)
    if target_status == current_status:
        return media
    if target_status not in _TRANSITIONS[current_status]:
        raise InvalidMediaTransition(f"Cannot transition media from {current_status} to {target_status}")

    encoding_status = encoding_status_for(target_status)
    Media.objects.filter(pk=media.pk).update(
        processing_status=target_status,
        encoding_status=encoding_status,
    )
    media.processing_status = target_status
    media.encoding_status = encoding_status
    return media


@transaction.atomic
def activate_asset_version(media_id: int, version_id: UUID) -> Media:
    media = Media.objects.select_for_update().get(pk=media_id)
    candidate = MediaAssetVersion.objects.select_for_update().filter(pk=version_id).first()
    if candidate is None:
        raise InvalidAssetActivation("Asset version does not exist")
    if candidate.media_id != media.id:
        raise InvalidAssetActivation("Asset version belongs to another media")
    if candidate.status != MediaAssetVersion.Status.CANDIDATE:
        raise InvalidAssetActivation("Asset version is not a candidate")
    if not MediaAsset.objects.filter(
        version=candidate,
        kind=MediaAsset.Kind.HLS_MASTER,
        s3_key=candidate.manifest_key,
    ).exists():
        raise InvalidAssetActivation("Candidate manifest is not registered")

    previous_id = media.active_asset_version_id
    if previous_id is not None and previous_id != candidate.id:
        previous = MediaAssetVersion.objects.select_for_update().get(pk=previous_id)
        if previous.media_id != media.id or previous.status != MediaAssetVersion.Status.ACTIVE:
            raise InvalidAssetActivation("Current active asset pointer is inconsistent")
        MediaAssetVersion.objects.filter(pk=previous.pk).update(status=MediaAssetVersion.Status.RETIRED)

    activated_at = timezone.now()
    MediaAssetVersion.objects.filter(pk=candidate.pk).update(
        status=MediaAssetVersion.Status.ACTIVE,
        activated_at=activated_at,
    )
    Media.objects.filter(pk=media.pk).update(
        active_asset_version=candidate,
        processing_status=MediaProcessingStatus.READY,
        encoding_status=encoding_status_for(MediaProcessingStatus.READY),
    )
    media.active_asset_version_id = candidate.id
    media.processing_status = MediaProcessingStatus.READY
    media.encoding_status = encoding_status_for(MediaProcessingStatus.READY)
    return media


@transaction.atomic
def update_media_metadata(
    media_id: int,
    expected_revision: int,
    changes: Mapping[str, Any],
    source: str,
) -> Media:
    media = Media.objects.select_for_update().get(pk=media_id)
    _require_revision(media, expected_revision)
    if source not in _ALLOWED_METADATA_SOURCES:
        raise InvalidMetadataUpdate(f"Unsupported metadata source: {source}")
    unsupported = set(changes) - _ALLOWED_METADATA_FIELDS
    if unsupported:
        raise InvalidMetadataUpdate(f"Unsupported metadata fields: {', '.join(sorted(unsupported))}")

    normalized = _normalize_changes(changes)
    sources = dict(media.metadata_sources)
    scalar_updates: dict[str, Any] = {}
    relationship_updates: dict[str, set[int]] = {}
    provenance_changed = False

    for field_name, incoming_value in normalized.items():
        current_value = _current_metadata_value(media, field_name)
        current_source = sources.get(field_name)
        if not _source_may_write(current_value, current_source, source):
            continue
        if current_value != incoming_value:
            if field_name in _SCALAR_METADATA_FIELDS:
                scalar_updates[field_name] = incoming_value
            else:
                relationship_updates[field_name] = incoming_value
        if current_source != source:
            sources[field_name] = source
            provenance_changed = True

    if not scalar_updates and not relationship_updates and not provenance_changed:
        return _reload_media(media.id)

    if "state" in scalar_updates:
        state = scalar_updates["state"]
        scalar_updates["listable"] = bool(
            state == "public" and media.encoding_status == "success" and media.is_reviewed
        )
    new_revision = media.revision + 1
    Media.objects.filter(pk=media.pk).update(
        **scalar_updates,
        metadata_sources=sources,
        revision=new_revision,
    )
    if "category_ids" in relationship_updates:
        media.category.set(relationship_updates["category_ids"])
    if "tag_ids" in relationship_updates:
        media.tags.set(relationship_updates["tag_ids"])
    return _reload_media(media.id)


@transaction.atomic
def request_media_deletion(media_id: int, expected_revision: int) -> Media:
    media = Media.objects.select_for_update().get(pk=media_id)
    _require_revision(media, expected_revision)
    if media.deletion_status in {"pending", "deleting"}:
        return media
    if media.deletion_status not in {"none", "failed"}:
        raise InvalidDeletionRequest(f"Cannot request deletion from {media.deletion_status}")

    new_revision = media.revision + 1
    Media.objects.filter(pk=media.pk).update(
        deletion_status="pending",
        listable=False,
        revision=new_revision,
    )
    media.deletion_status = "pending"
    media.listable = False
    media.revision = new_revision
    return media


def _require_revision(media: Media, expected_revision: int) -> None:
    if media.revision == expected_revision:
        return
    raise MediaRevisionConflict(media.revision, _metadata_snapshot(media))


def _metadata_snapshot(media: Media) -> dict[str, Any]:
    return {
        "title": media.title,
        "description": media.description,
        "state": media.state,
        "category_ids": sorted(media.category.values_list("id", flat=True)),
        "tag_ids": sorted(media.tags.values_list("id", flat=True)),
    }


def _normalize_changes(changes: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for field_name, value in changes.items():
        if field_name == "title":
            if not isinstance(value, str):
                raise InvalidMetadataUpdate("Title must be a string")
            normalized[field_name] = strip_tags(value)[:100]
        elif field_name == "description":
            if not isinstance(value, str):
                raise InvalidMetadataUpdate("Description must be a string")
            normalized[field_name] = strip_tags(value)
        elif field_name == "state":
            allowed_states = {choice[0] for choice in MEDIA_STATES}
            if value not in allowed_states:
                raise InvalidMetadataUpdate("Unsupported media visibility state")
            normalized[field_name] = value
        elif field_name == "category_ids":
            normalized[field_name] = _validated_ids(Category, value, field_name)
        elif field_name == "tag_ids":
            normalized[field_name] = _validated_ids(Tag, value, field_name)
    return normalized


def _validated_ids(model, values: Any, field_name: str) -> set[int]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Collection):
        raise InvalidMetadataUpdate(f"{field_name} must be a collection of IDs")
    try:
        requested = {int(value) for value in values}
    except (TypeError, ValueError) as exc:
        raise InvalidMetadataUpdate(f"{field_name} contains an invalid ID") from exc
    existing = set(model.objects.filter(id__in=requested).values_list("id", flat=True))
    if existing != requested:
        raise InvalidMetadataUpdate(f"{field_name} contains an unknown ID")
    return requested


def _current_metadata_value(media: Media, field_name: str) -> Any:
    if field_name == "category_ids":
        return set(media.category.values_list("id", flat=True))
    if field_name == "tag_ids":
        return set(media.tags.values_list("id", flat=True))
    return getattr(media, field_name)


def _source_may_write(current_value: Any, current_source: str | None, incoming_source: str) -> bool:
    if incoming_source == "admin":
        return True
    if incoming_source not in _AUTOMATIC_SOURCES or current_source == "admin":
        return False
    if current_source == incoming_source:
        return True
    return current_value in {None, ""} if not isinstance(current_value, set) else not current_value


def _reload_media(media_id: int) -> Media:
    return Media.objects.prefetch_related("category", "tags").get(pk=media_id)
