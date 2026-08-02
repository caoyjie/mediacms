import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone


class JobSourceType(models.TextChoices):
    UPLOAD = "upload", "Upload"
    HLS_ZIP = "hls_zip", "HLS ZIP"
    YOUTUBE = "youtube", "YouTube"


class JobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    FAILED = "failed", "Failed"
    CANCELED = "canceled", "Canceled"
    COMPLETED = "completed", "Completed"


class AttemptStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    FAILED = "failed", "Failed"
    CANCELED = "canceled", "Canceled"
    COMPLETED = "completed", "Completed"


class CleanupStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    FAILED = "failed", "Failed"
    COMPLETED = "completed", "Completed"


class CheckpointStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    COMPLETED = "completed", "Completed"
    AVAILABLE = "available", "Available"
    UNAVAILABLE = "unavailable", "Unavailable"
    FAILED_RETRYABLE = "failed_retryable", "Failed retryable"


class ArtifactPurpose(models.TextChoices):
    UPLOAD_SOURCE = "upload_source", "Upload source"
    ORIGINAL = "original", "Original"
    CANDIDATE = "candidate", "Candidate"


class ArtifactCleanupStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RETAINED = "retained", "Retained"
    DELETED = "deleted", "Deleted"
    FAILED = "failed", "Failed"


class MediaIngestionJobQuerySet(models.QuerySet):
    def queued(self):
        from .uploads import BrowserUploadSession

        incomplete_upload = BrowserUploadSession.objects.filter(
            job_id=OuterRef("pk"),
        ).exclude(status="completed")
        return (
            self.filter(status=JobStatus.QUEUED)
            .annotate(has_incomplete_upload=Exists(incomplete_upload))
            .filter(has_incomplete_upload=False)
            .order_by("queued_at", "id")
        )


class MediaIngestionJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    media = models.ForeignKey(
        "Media",
        on_delete=models.SET_NULL,
        related_name="ingestion_jobs",
        blank=True,
        null=True,
    )
    media_title_snapshot = models.CharField(max_length=100)
    source_type = models.CharField(max_length=20, choices=JobSourceType.choices)
    status = models.CharField(
        max_length=20,
        choices=JobStatus.choices,
        default=JobStatus.QUEUED,
        db_index=True,
    )
    stage = models.CharField(max_length=64, blank=True)
    progress = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=(MinValueValidator(0), MaxValueValidator(100)),
    )
    cancel_requested = models.BooleanField(default=False)
    cleanup_status = models.CharField(
        max_length=20,
        choices=CleanupStatus.choices,
        default=CleanupStatus.PENDING,
        db_index=True,
    )
    source_metadata = models.JSONField(default=dict, blank=True)
    safe_error = models.TextField(blank=True)
    queued_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MediaIngestionJobQuerySet.as_manager()

    class Meta:
        ordering = ("queued_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(progress__gte=0, progress__lte=100),
                name="files_job_progress_range",
            )
        ]
        indexes = [models.Index(fields=("status", "queued_at", "id"), name="files_job_fifo_idx")]

    def __str__(self):
        return f"{self.id}:{self.source_type}:{self.status}"


class MediaJobAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        MediaIngestionJob,
        on_delete=models.PROTECT,
        related_name="attempts",
    )
    sequence = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20,
        choices=AttemptStatus.choices,
        default=AttemptStatus.QUEUED,
        db_index=True,
    )
    celery_task_id = models.CharField(max_length=255, blank=True)
    template_name = models.CharField(max_length=255, blank=True)
    template_version = models.CharField(max_length=255, blank=True)
    client_request_token = models.CharField(max_length=64, blank=True)
    submission_intent_at = models.DateTimeField(blank=True, null=True)
    mediaconvert_job_id = models.CharField(max_length=255, blank=True)
    provider_status = models.CharField(max_length=64, blank=True)
    provider_phase = models.CharField(max_length=64, blank=True)
    provider_percent_complete = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=(MinValueValidator(0), MaxValueValidator(100)),
        blank=True,
        null=True,
    )
    next_poll_at = models.DateTimeField(blank=True, null=True)
    provider_last_changed_at = models.DateTimeField(blank=True, null=True)
    provider_unchanged_count = models.PositiveIntegerField(default=0)
    checkpoint_evidence = models.JSONField(default=dict, blank=True)
    diagnostic_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("job", "sequence")
        indexes = [
            models.Index(
                fields=("status", "next_poll_at"),
                name="files_attempt_due_idx",
            )
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("job", "sequence"),
                name="files_attempt_job_sequence_uniq",
            )
        ]

    def __str__(self):
        return f"{self.job_id}:{self.sequence}:{self.status}"


class AttemptArtifact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(
        MediaJobAttempt,
        on_delete=models.CASCADE,
        related_name="artifacts",
    )
    purpose = models.CharField(max_length=32, choices=ArtifactPurpose.choices)
    s3_key = models.CharField(max_length=1500)
    size_bytes = models.PositiveBigIntegerField()
    content_type = models.CharField(max_length=255)
    checksum = models.CharField(max_length=255)
    cleanup_status = models.CharField(
        max_length=20,
        choices=ArtifactCleanupStatus.choices,
        default=ArtifactCleanupStatus.PENDING,
        db_index=True,
    )
    safe_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("attempt", "created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("attempt", "s3_key"),
                name="files_artifact_attempt_key_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    Q(s3_key__startswith="uploads/")
                    | Q(s3_key__startswith="originals/")
                    | Q(s3_key__startswith="candidates/")
                ),
                name="files_artifact_managed_root",
            ),
        ]

    def __str__(self):
        return f"{self.attempt_id}:{self.purpose}:{self.cleanup_status}"


class MediaJobWarning(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(
        MediaJobAttempt,
        on_delete=models.CASCADE,
        related_name="warnings",
    )
    code = models.CharField(max_length=64)
    message = models.TextField()
    acknowledged_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("attempt", "created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("attempt", "code"),
                name="files_warning_attempt_code_uniq",
            )
        ]

    def __str__(self):
        return f"{self.attempt_id}:{self.code}"


class MediaJobCheckpoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(
        MediaJobAttempt,
        on_delete=models.CASCADE,
        related_name="checkpoints",
    )
    name = models.CharField(max_length=64)
    status = models.CharField(
        max_length=20,
        choices=CheckpointStatus.choices,
        default=CheckpointStatus.PENDING,
        db_index=True,
    )
    input_fingerprint = models.CharField(max_length=255, blank=True)
    evidence = models.JSONField(default=dict, blank=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("attempt", "created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("attempt", "name"),
                name="files_checkpoint_attempt_name_uniq",
            )
        ]

    def __str__(self):
        return f"{self.attempt_id}:{self.name}:{self.status}"


class ProcessingLease(models.Model):
    singleton_key = models.CharField(primary_key=True, max_length=20, default="default", editable=False)
    job = models.ForeignKey(
        MediaIngestionJob,
        on_delete=models.PROTECT,
        related_name="processing_leases",
        blank=True,
        null=True,
    )
    attempt = models.ForeignKey(
        MediaJobAttempt,
        on_delete=models.PROTECT,
        related_name="processing_leases",
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
                name="files_processing_lease_default_key",
            )
        ]

    def __str__(self):
        return self.singleton_key
