from django.db import models


class MediaProcessingStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    QUEUED = "queued", "Queued"
    PROCESSING = "processing", "Processing"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class StorageBackend(models.TextChoices):
    LEGACY_LOCAL = "legacy_local", "Legacy local"
    AWS = "aws", "AWS"


class DeletionStatus(models.TextChoices):
    NONE = "none", "None"
    PENDING = "pending", "Pending"
    DELETING = "deleting", "Deleting"
    FAILED = "failed", "Failed"
    COMPLETED = "completed", "Completed"


ENCODING_STATUS_BY_PROCESSING_STATUS = {
    MediaProcessingStatus.DRAFT: "pending",
    MediaProcessingStatus.QUEUED: "pending",
    MediaProcessingStatus.PROCESSING: "running",
    MediaProcessingStatus.READY: "success",
    MediaProcessingStatus.FAILED: "fail",
}


def encoding_status_for(processing_status: str) -> str:
    try:
        return ENCODING_STATUS_BY_PROCESSING_STATUS[processing_status]
    except KeyError as exc:
        raise ValueError(f"Unknown media processing status: {processing_status}") from exc
