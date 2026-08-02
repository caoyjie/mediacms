"""Checkpoint-aware YouTube import orchestration.

This module deliberately keeps yt-dlp and local files outside the request path.
Callers invoke one operation from the single processing queue and persist the
returned checkpoint evidence before moving to the next operation.
"""

from dataclasses import dataclass
import hashlib
import tempfile

from django.conf import settings
from django.utils import timezone

from files.models import AttemptArtifact, MediaJobCheckpoint
from files.models.ingestion import ArtifactCleanupStatus, ArtifactPurpose, CheckpointStatus
from files.services.processing_storage import ObjectEvidence
from files.services.youtube import extract_info, download_source
from files.services.youtube_cookies import materialize_cookie


@dataclass(frozen=True, slots=True)
class YouTubeMetadata:
    video_id: str
    title: str
    description: str
    duration: int
    thumbnail: str


def discover(url, *, cookie_version=None):
    with materialize_cookie(cookie_version) as cookie_file:
        info = extract_info(url, cookie_file=cookie_file)
    return YouTubeMetadata(
        video_id=str(info.get("id", "")),
        title=str(info.get("title", ""))[:100],
        description=str(info.get("description", "")),
        duration=int(info.get("duration") or 0),
        thumbnail=str(info.get("thumbnail", "")),
    ), info


def download_to_attempt(attempt, url, *, cookie_version=None, now=None):
    now = now or timezone.now()
    with tempfile.TemporaryDirectory(dir=settings.TEMP_DIRECTORY, prefix=f"yt-{attempt.id}-") as directory:
        with materialize_cookie(cookie_version, directory=directory) as cookie_file:
            source_path, info = download_source(url, directory, cookie_file=cookie_file)
            key = f"originals/{attempt.job.media_id}/{attempt.id}/source{source_path.suffix.lower()}"
            import boto3
            client = boto3.client("s3", region_name=settings.AWS_REGION)
            client.upload_file(str(source_path), settings.AWS_MEDIA_BUCKET, key)
            head = client.head_object(Bucket=settings.AWS_MEDIA_BUCKET, Key=key, ChecksumMode="ENABLED")
            checksum = head.get("ChecksumSHA256") or hashlib.sha256(source_path.read_bytes()).hexdigest()
            evidence = ObjectEvidence(key, int(head["ContentLength"]), head.get("ContentType", "application/octet-stream"), f"sha256:{checksum}")
            AttemptArtifact.objects.update_or_create(
                attempt=attempt,
                s3_key=key,
                defaults={
                    "purpose": ArtifactPurpose.ORIGINAL,
                    "size_bytes": evidence.size,
                    "content_type": evidence.content_type,
                    "checksum": evidence.checksum,
                    "cleanup_status": ArtifactCleanupStatus.RETAINED,
                },
            )
            MediaJobCheckpoint.objects.update_or_create(
                attempt=attempt,
                name="source_verified",
                defaults={"status": CheckpointStatus.COMPLETED, "evidence": {"s3_key": key, "checksum": evidence.checksum}, "completed_at": now},
            )
            return evidence, info


def subtitle_checkpoint(attempt, *, status, evidence=None, now=None):
    if status not in {CheckpointStatus.AVAILABLE, CheckpointStatus.UNAVAILABLE, CheckpointStatus.FAILED_RETRYABLE}:
        raise ValueError("invalid subtitle checkpoint status")
    return MediaJobCheckpoint.objects.update_or_create(
        attempt=attempt,
        name="subtitles",
        defaults={"status": status, "evidence": evidence or {}, "completed_at": now or timezone.now()},
    )[0]
