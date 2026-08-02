import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q


DEFAULT_UPLOAD_PART_SIZE = 16 * 1024 * 1024


class BrowserUploadSourceKind(models.TextChoices):
    FILE = "file", "File"
    HLS = "hls", "HLS package"


class BrowserUploadStatus(models.TextChoices):
    WAITING = "waiting", "Waiting"
    UPLOADING = "uploading", "Uploading"
    PAUSED = "paused", "Paused"
    VERIFYING = "verifying", "Verifying"
    COMPLETED = "completed", "Completed"
    CANCELED = "canceled", "Canceled"
    EXPIRED = "expired", "Expired"
    FAILED = "failed", "Failed"


class BrowserUploadStrategy(models.TextChoices):
    MULTIPART = "multipart", "Multipart"
    SINGLE_PUT = "single_put", "Single PUT"


class BrowserUploadObjectStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    UPLOADING = "uploading", "Uploading"
    UPLOADED = "uploaded", "Uploaded"
    VERIFIED = "verified", "Verified"
    ABORTED = "aborted", "Aborted"


class BrowserUploadSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.OneToOneField(
        "MediaIngestionJob",
        on_delete=models.PROTECT,
        related_name="browser_upload_session",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="browser_upload_sessions",
    )
    source_kind = models.CharField(
        max_length=20,
        choices=BrowserUploadSourceKind.choices,
    )
    status = models.CharField(
        max_length=20,
        choices=BrowserUploadStatus.choices,
        default=BrowserUploadStatus.WAITING,
        db_index=True,
    )
    expected_total_size = models.PositiveBigIntegerField()
    expected_file_count = models.PositiveIntegerField(default=1)
    confirmed_bytes = models.PositiveBigIntegerField(default=0)
    confirmed_file_count = models.PositiveIntegerField(default=0)
    file_fingerprint = models.CharField(max_length=255, blank=True)
    part_size = models.PositiveIntegerField(default=DEFAULT_UPLOAD_PART_SIZE)
    create_idempotency_key = models.CharField(max_length=255, unique=True)
    completion_idempotency_key = models.CharField(max_length=255, blank=True)
    revision = models.PositiveBigIntegerField(default=1)
    expires_at = models.DateTimeField(blank=True, null=True, db_index=True)
    safe_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(expected_total_size__gt=0),
                name="files_upload_expected_size_positive",
            ),
            models.CheckConstraint(
                condition=Q(expected_file_count__gt=0),
                name="files_upload_expected_count_positive",
            ),
            models.CheckConstraint(
                condition=Q(confirmed_bytes__lte=F("expected_total_size")),
                name="files_upload_confirmed_size_lte_expected",
            ),
            models.CheckConstraint(
                condition=Q(confirmed_file_count__lte=F("expected_file_count")),
                name="files_upload_confirmed_count_lte_expected",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="files_upload_revision_positive",
            ),
        ]

    @property
    def upload_prefix(self):
        return f"uploads/{self.job_id}/{self.id}/"

    def __str__(self):
        return f"{self.id}:{self.source_kind}:{self.status}"


class BrowserUploadObject(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        BrowserUploadSession,
        on_delete=models.CASCADE,
        related_name="upload_objects",
    )
    relative_path = models.CharField(max_length=1024)
    s3_key = models.CharField(max_length=1500, unique=True)
    strategy = models.CharField(
        max_length=20,
        choices=BrowserUploadStrategy.choices,
    )
    status = models.CharField(
        max_length=20,
        choices=BrowserUploadObjectStatus.choices,
        default=BrowserUploadObjectStatus.PENDING,
        db_index=True,
    )
    expected_size = models.PositiveBigIntegerField()
    content_type = models.CharField(max_length=255)
    multipart_upload_id = models.CharField(max_length=1024, blank=True)
    checksum = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("session", "relative_path", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("session", "relative_path"),
                name="files_upload_object_session_path_uniq",
            ),
            models.CheckConstraint(
                condition=Q(expected_size__gt=0),
                name="files_upload_object_size_positive",
            ),
        ]

    def __str__(self):
        return f"{self.session_id}:{self.relative_path}:{self.status}"


class BrowserUploadPart(models.Model):
    id = models.BigAutoField(primary_key=True)
    upload_object = models.ForeignKey(
        BrowserUploadObject,
        on_delete=models.CASCADE,
        related_name="parts",
    )
    part_number = models.PositiveIntegerField()
    etag = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("upload_object", "part_number")
        constraints = [
            models.UniqueConstraint(
                fields=("upload_object", "part_number"),
                name="files_upload_part_object_number_uniq",
            ),
            models.CheckConstraint(
                condition=Q(part_number__gte=1, part_number__lte=10_000),
                name="files_upload_part_number_range",
            ),
            models.CheckConstraint(
                condition=Q(size__gt=0),
                name="files_upload_part_size_positive",
            ),
        ]

    def __str__(self):
        return f"{self.upload_object_id}:{self.part_number}"


class BrowserUploadLease(models.Model):
    singleton_key = models.CharField(
        primary_key=True,
        max_length=20,
        default="default",
        editable=False,
    )
    session = models.ForeignKey(
        BrowserUploadSession,
        on_delete=models.PROTECT,
        related_name="upload_leases",
        blank=True,
        null=True,
    )
    job = models.ForeignKey(
        "MediaIngestionJob",
        on_delete=models.PROTECT,
        related_name="upload_leases",
        blank=True,
        null=True,
    )
    owner_token = models.CharField(max_length=255, blank=True)
    heartbeat_at = models.DateTimeField(blank=True, null=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(singleton_key="default"),
                name="files_upload_lease_default_key",
            )
        ]

    def __str__(self):
        return self.singleton_key
