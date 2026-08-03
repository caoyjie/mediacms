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

from files.models import AttemptArtifact, MediaJobCheckpoint, MediaIngestionJob
from files.models.ingestion import ArtifactCleanupStatus, ArtifactPurpose, CheckpointStatus
from files.services.processing_storage import ObjectEvidence
from files.services.youtube import CaptionTrack, choose_caption_tracks, discovered_caption_tracks, extract_info, download_source, fetch_caption_text
from files.services.subtitles import build_bilingual_webvtt, normalize_caption_payload, parse_webvtt
from files.services.youtube_cookies import materialize_cookie
from files.services.youtube_cookies import latest_cookie


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


def upload_subtitle_tracks(attempt, *, client=None):
    checkpoint = MediaJobCheckpoint.objects.filter(
        attempt=attempt,
        name="subtitles",
        status=CheckpointStatus.AVAILABLE,
    ).first()
    if checkpoint is None:
        return 0
    if client is None:
        import boto3
        client = boto3.client("s3", region_name=settings.AWS_REGION)
    count = 0
    prefix = f"candidates/{attempt.job.media_id}/{attempt.id}/subtitles/"
    for language, text in (checkpoint.evidence.get("tracks") or {}).items():
        payload = text.encode("utf-8")
        key = f"{prefix}{language}.vtt"
        existing = AttemptArtifact.objects.filter(attempt=attempt, s3_key=key, purpose=ArtifactPurpose.CANDIDATE).first()
        if existing is not None and existing.size_bytes == len(payload) and existing.checksum == f"sha256:{hashlib.sha256(payload).hexdigest()}":
            continue
        client.put_object(
            Bucket=settings.AWS_MEDIA_BUCKET,
            Key=key,
            Body=payload,
            ContentType="text/vtt; charset=utf-8",
            ChecksumAlgorithm="SHA256",
        )
        evidence = client.head_object(Bucket=settings.AWS_MEDIA_BUCKET, Key=key)
        if int(evidence.get("ContentLength", -1)) != len(payload):
            raise ValueError("uploaded subtitle size verification failed")
        checksum = hashlib.sha256(payload).hexdigest()
        AttemptArtifact.objects.update_or_create(
            attempt=attempt,
            s3_key=key,
            defaults={
                "purpose": ArtifactPurpose.CANDIDATE,
                "size_bytes": len(payload),
                "content_type": "text/vtt",
                "checksum": f"sha256:{checksum}",
                "cleanup_status": ArtifactCleanupStatus.PENDING,
            },
        )
        count += 1
    return count


def run_youtube_step(attempt, *, now=None):
    """Run one bounded YouTube checkpoint operation for the global queue."""
    now = now or timezone.now()
    url = (attempt.job.source_metadata or {}).get("url")
    if not url:
        raise ValueError("YouTube job has no source URL")
    cookie_id = (attempt.job.source_metadata or {}).get("cookie_version_id")
    cookie = None
    if cookie_id:
        from files.models import YouTubeCookieVersion
        cookie = YouTubeCookieVersion.objects.filter(pk=cookie_id, status="active").first()
    else:
        cookie = latest_cookie()
    metadata_checkpoint = MediaJobCheckpoint.objects.filter(attempt=attempt, name="metadata").first()
    if metadata_checkpoint is None:
        try:
            metadata, info = discover(url, cookie_version=cookie)
        except Exception as error:
            kind = getattr(error, "kind", "unknown")
            action = "cookies" if kind == "cookies" else "retry" if kind == "retryable" else "review"
            attempt.status = "failed"
            attempt.diagnostic_error = "YouTube download requires updated cookies." if action == "cookies" else "YouTube metadata discovery failed."
            attempt.save(update_fields=("status", "diagnostic_error", "updated_at"))
            MediaIngestionJob.objects.filter(pk=attempt.job_id).update(status="failed", stage="action_required" if action == "cookies" else "failed", safe_error=attempt.diagnostic_error)
            return "failed"
        MediaJobCheckpoint.objects.create(
            attempt=attempt, name="metadata", status="completed",
            evidence={
                "video_id": metadata.video_id,
                "title": metadata.title,
                "duration": metadata.duration,
                "thumbnail": metadata.thumbnail,
                "caption_tracks": {
                    language: {"url": track.url, "kind": track.kind}
                    for language, track in choose_caption_tracks(discovered_caption_tracks(info)).items()
                },
            },
            completed_at=now,
        )
        source_metadata = dict(attempt.job.source_metadata or {})
        source_metadata["discovered"] = {
            "video_id": metadata.video_id,
            "title": metadata.title,
            "description": metadata.description,
            "duration": metadata.duration,
            "thumbnail": metadata.thumbnail,
        }
        MediaIngestionJob.objects.filter(pk=attempt.job_id).update(
            source_metadata=source_metadata,
            media_title_snapshot=metadata.title,
            stage="metadata_ready",
        )
        if attempt.job.media and not (attempt.job.media.metadata_sources or {}).get("title"):
            attempt.job.media.title = metadata.title
            attempt.job.media.description = metadata.description
            attempt.job.media.duration = metadata.duration
            attempt.job.media.metadata_sources = {"title": "youtube", "description": "youtube", "duration": "youtube"}
            attempt.job.media.save(update_fields=("title", "description", "duration", "metadata_sources", "edit_date"))
        return "metadata"
    source_checkpoint = MediaJobCheckpoint.objects.filter(attempt=attempt, name="source_verified").first()
    if source_checkpoint is None:
        download_to_attempt(attempt, url, cookie_version=cookie, now=now)
        return "download"
    subtitle = MediaJobCheckpoint.objects.filter(attempt=attempt, name="subtitles").first()
    if subtitle is None:
        tracks = {}
        for language, item in (metadata_checkpoint.evidence.get("caption_tracks") or {}).items():
            tracks[language] = CaptionTrack(item["url"], language, item.get("kind", "manual"))
        if not tracks:
            subtitle_checkpoint(attempt, status=CheckpointStatus.UNAVAILABLE, evidence={"reason": "no subtitles were offered"}, now=now)
            return "subtitles"
        try:
            with materialize_cookie(cookie, directory=settings.TEMP_DIRECTORY) as cookie_file:
                sources = {
                    language: normalize_caption_payload(fetch_caption_text(track, cookie_file=cookie_file))
                    for language, track in tracks.items()
                }
            published = {language: text for language, text in sources.items()}
            if "zh" in sources and "en" in sources:
                published["bilingual"] = build_bilingual_webvtt(parse_webvtt(sources["zh"]), parse_webvtt(sources["en"]))
            subtitle_checkpoint(
                attempt,
                status=CheckpointStatus.AVAILABLE,
                evidence={"tracks": published, "languages": sorted(published)},
                now=now,
            )
        except (OSError, UnicodeError, ValueError):
            subtitle_checkpoint(attempt, status=CheckpointStatus.FAILED_RETRYABLE, evidence={"reason": "subtitle fetch or parsing failed"}, now=now)
        return "subtitles"
    upload_subtitle_tracks(attempt)
    return "ready"
