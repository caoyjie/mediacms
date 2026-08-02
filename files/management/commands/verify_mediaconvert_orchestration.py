import json
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import boto3
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from files.models import (
    AttemptArtifact,
    BrowserUploadObject,
    BrowserUploadSession,
    Media,
    MediaAsset,
    MediaAssetVersion,
    MediaIngestionJob,
    MediaJobAttempt,
    MediaJobCheckpoint,
    ProcessingLease,
)
from files.models.domain import StorageBackend
from files.models.ingestion import CheckpointStatus, JobStatus
from files.models.uploads import (
    BrowserUploadObjectStatus,
    BrowserUploadStatus,
    BrowserUploadStrategy,
)
from files.services.processing_runner import run_processing_tick
from files.services.processing_storage import ProcessingStorageGateway
from files.services.upload_sessions import promote_file_original
from files.services.processing_queue import enqueue_job
from users.models import SiteAdministrator


@dataclass(frozen=True, slots=True)
class FixtureResult:
    path: Path
    duration_seconds: float


def build_trim_command(source, destination, media_type):
    if media_type == "video":
        duration = "20"
        maps = ["-map", "0:v:0", "-map", "0:a?"]
    elif media_type == "audio":
        duration = "30"
        maps = ["-map", "0:a:0"]
    else:
        raise ValueError("media_type must be video or audio")
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-t",
        duration,
        *maps,
        "-c",
        "copy",
        str(destination),
    ]


def _compatibility_command(source, destination, media_type):
    if media_type == "video":
        return [
            "ffmpeg", "-y", "-i", str(source), "-t", "20",
            "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264",
            "-preset", "veryfast", "-c:a", "aac", str(destination),
        ]
    return [
        "ffmpeg", "-y", "-i", str(source), "-t", "30",
        "-map", "0:a:0", "-c:a", "aac", str(destination),
    ]


def _run(command):
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )


def _probe(path, runner=_run):
    result = runner([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(path),
    ])
    try:
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("ffprobe returned invalid fixture evidence") from error
    if duration <= 0:
        raise ValueError("fixture duration must be positive")
    return duration


def prepare_fixture(source, media_type, workdir, *, runner=_run):
    source = Path(source)
    if not source.exists() or source.is_symlink() or not source.is_file():
        raise ValueError("source must be a regular file")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    destination = workdir / ("video.mp4" if media_type == "video" else "audio.m4a")
    try:
        runner(build_trim_command(source, destination, media_type))
        duration = _probe(destination, runner)
    except Exception:
        if destination.exists():
            destination.unlink()
        runner(_compatibility_command(source, destination, media_type))
        duration = _probe(destination, runner)
    return FixtureResult(destination, duration)


@contextmanager
def temporary_fixture(source, media_type, work_parent, *, runner=_run):
    work_parent = Path(work_parent)
    work_parent.mkdir(parents=True, exist_ok=True)
    private = Path(tempfile.mkdtemp(prefix="mediacms-acceptance-", dir=work_parent))
    try:
        yield prepare_fixture(source, media_type, private, runner=runner)
    finally:
        shutil.rmtree(private, ignore_errors=True)


def _content_type(media_type):
    return "video/mp4" if media_type == "video" else "audio/mp4"


class Command(BaseCommand):
    help = "Run disposable serial MediaConvert acceptance for one video and one audio source."

    def add_arguments(self, parser):
        parser.add_argument("--video-source", required=True)
        parser.add_argument("--audio-source", required=True)
        parser.add_argument("--stack", default="mediacms-dev")
        parser.add_argument("--region", default="us-east-1")
        parser.add_argument("--work-parent", default=None)
        parser.add_argument("--max-ticks", type=int, default=240)

    def handle(self, *args, **options):
        if not settings.AWS_MEDIA_BUCKET:
            raise CommandError("AWS_MEDIA_BUCKET is not configured")
        binding = SiteAdministrator.get_solo()
        if binding is None:
            raise CommandError("Site administrator is not initialized")
        work_parent = options["work_parent"] or tempfile.gettempdir()
        summary = []
        with tempfile.TemporaryDirectory(prefix="mediacms-acceptance-root-", dir=work_parent) as root:
            try:
                with temporary_fixture(options["video_source"], "video", root) as video:
                    summary.append(self._run_media("video", video, options))
                with temporary_fixture(options["audio_source"], "audio", root) as audio:
                    summary.append(self._run_media("audio", audio, options))
            finally:
                # temporary_fixture and TemporaryDirectory remove all local bytes here.
                pass
        self.stdout.write("MediaConvert acceptance PASS")
        for item in summary:
            self.stdout.write(
                f"{item['media_type']}: job_id={item['job_id']} "
                f"template_version={item['template_version']} status={item['status']}"
            )

    def _run_media(self, media_type, fixture, options):
        owner = SiteAdministrator.get_solo().user
        media, job, session, upload_key = self._create_case(owner, media_type, fixture)
        storage = ProcessingStorageGateway()
        created_keys = {upload_key}
        try:
            client = boto3.client("s3", region_name=options["region"])
            with fixture.path.open("rb") as stream:
                client.put_object(
                    Bucket=settings.AWS_MEDIA_BUCKET,
                    Key=upload_key,
                    Body=stream,
                    ContentType=_content_type(media_type),
                )
            evidence = storage.head_exact(upload_key)
            BrowserUploadObject.objects.filter(session=session).update(
                status=BrowserUploadObjectStatus.VERIFIED,
                checksum=evidence.checksum,
            )
            promoted = promote_file_original(session.id, storage)
            attempt = MediaJobAttempt.objects.get(job=job, sequence=1)
            MediaJobCheckpoint.objects.update_or_create(
                attempt=attempt,
                name="source_verified",
                defaults={
                    "status": CheckpointStatus.COMPLETED,
                    "evidence": {
                        "s3_key": promoted.key,
                        "size": promoted.size,
                        "content_type": promoted.content_type,
                        "checksum": promoted.checksum,
                    },
                    "completed_at": timezone.now(),
                },
            )
            enqueue_job(job.id)
            created_keys.update(
                AttemptArtifact.objects.filter(attempt=attempt).values_list("s3_key", flat=True)
            )
            result = self._run_until_complete(job.id, options["max_ticks"])
            attempt.refresh_from_db()
            provider_job_id = attempt.mediaconvert_job_id
            template_version = attempt.template_version
            created_keys.update(
                AttemptArtifact.objects.filter(attempt=attempt).values_list("s3_key", flat=True)
            )
            return {
                "media_type": media_type,
                "job_id": provider_job_id,
                "template_version": template_version,
                "status": result,
            }
        finally:
            self._cleanup_case(media.id, job.id, created_keys, storage)

    def _create_case(self, owner, media_type, fixture):
        with transaction.atomic():
            media = Media.objects.create(
                title=f"AWS acceptance {media_type}",
                user=owner,
                media_type=media_type,
                storage_backend=StorageBackend.AWS,
                processing_status="queued",
                encoding_status="pending",
            )
            job = MediaIngestionJob.objects.create(
                media=media,
                media_title_snapshot=media.title,
                source_type="upload",
                status=JobStatus.QUEUED,
                stage="waiting_upload",
            )
            session = BrowserUploadSession.objects.create(
                job=job,
                owner=owner,
                source_kind="file",
                expected_total_size=fixture.path.stat().st_size,
                expected_file_count=1,
                file_fingerprint=f"acceptance-{media.id}",
                create_idempotency_key=f"acceptance-create-{media.id}",
                status=BrowserUploadStatus.COMPLETED,
                confirmed_bytes=fixture.path.stat().st_size,
                confirmed_file_count=1,
            )
            suffix = ".mp4" if media_type == "video" else ".m4a"
            upload_key = f"uploads/{job.id}/{session.id}/source{suffix}"
            BrowserUploadObject.objects.create(
                session=session,
                relative_path=f"source{suffix}",
                s3_key=upload_key,
                strategy=BrowserUploadStrategy.SINGLE_PUT,
                status=BrowserUploadObjectStatus.UPLOADED,
                expected_size=fixture.path.stat().st_size,
                content_type=_content_type(media_type),
            )
            return media, job, session, upload_key

    def _run_until_complete(self, job_id, max_ticks):
        owner_token = f"acceptance:{job_id}"
        now = timezone.now()
        for _ in range(max_ticks):
            result = run_processing_tick(owner_token, now)
            job = MediaIngestionJob.objects.select_related("media").get(pk=job_id)
            if result.action == "done" and job.cleanup_status == "completed":
                if job.media.processing_status != "ready":
                    raise CommandError("Acceptance completed without a ready Media")
                return job.status
            if result.scheduled_delay:
                time.sleep(min(result.scheduled_delay, 60))
            now = timezone.now()
        raise CommandError("Acceptance exceeded the tick limit")

    def _cleanup_case(self, media_id, job_id, keys, storage):
        keys.update(
            AttemptArtifact.objects.filter(attempt__job_id=job_id).values_list(
                "s3_key", flat=True
            )
        )
        for key in sorted(keys):
            if key.startswith((f"uploads/{job_id}/", f"originals/{media_id}/", f"candidates/{media_id}/")):
                storage.delete_exact(key)
        with transaction.atomic():
            ProcessingLease.objects.filter(job_id=job_id).update(
                job=None,
                attempt=None,
                owner_token="",
                heartbeat_at=None,
                expires_at=None,
            )
            versions = MediaAssetVersion.objects.filter(media_id=media_id)
            MediaAsset.objects.filter(version__in=versions).delete()
            versions.delete()
            MediaJobAttempt.objects.filter(job_id=job_id).delete()
            BrowserUploadSession.objects.filter(job_id=job_id).delete()
            MediaIngestionJob.objects.filter(pk=job_id).delete()
            Media.objects.filter(pk=media_id).delete()
