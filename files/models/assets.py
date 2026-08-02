import uuid

from django.db import models


class MediaAssetVersion(models.Model):
    class Status(models.TextChoices):
        CANDIDATE = "candidate", "Candidate"
        ACTIVE = "active", "Active"
        RETIRED = "retired", "Retired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    media = models.ForeignKey(
        "Media",
        on_delete=models.PROTECT,
        related_name="asset_versions",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.CANDIDATE,
        db_index=True,
    )
    manifest_key = models.CharField(max_length=1024)
    activated_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "id")
        indexes = [models.Index(fields=("media", "status"), name="files_av_media_status_idx")]

    def __str__(self):
        return f"{self.media_id}:{self.id}:{self.status}"


class MediaAsset(models.Model):
    class Kind(models.TextChoices):
        ORIGINAL = "original", "Original"
        HLS_MASTER = "hls_master", "HLS master"
        HLS_VARIANT = "hls_variant", "HLS variant"
        HLS_SEGMENT = "hls_segment", "HLS segment"
        POSTER = "poster", "Poster"
        THUMBNAIL = "thumbnail", "Thumbnail"
        SUBTITLE = "subtitle", "Subtitle"
        AUDIO = "audio", "Audio"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(
        MediaAssetVersion,
        on_delete=models.CASCADE,
        related_name="assets",
    )
    kind = models.CharField(max_length=30, choices=Kind.choices, db_index=True)
    s3_key = models.CharField(max_length=1024)
    checksum = models.CharField(max_length=160, blank=True)
    size_bytes = models.PositiveBigIntegerField(blank=True, null=True)
    content_type = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("kind", "s3_key", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("version", "s3_key"),
                name="files_asset_key_per_version_uniq",
            )
        ]
        indexes = [models.Index(fields=("version", "kind"), name="files_asset_version_kind_idx")]

    def __str__(self):
        return f"{self.version_id}:{self.kind}:{self.s3_key}"
