from django.db import transaction
from django.utils import timezone

from files.models import (
    ArtifactPurpose,
    AttemptArtifact,
    Media,
    MediaAsset,
    MediaAssetVersion,
    MediaIngestionJob,
    MediaJobAttempt,
    MediaJobCheckpoint,
)
from files.models.ingestion import AttemptStatus, CheckpointStatus, JobStatus
from files.services.media_state import InvalidAssetActivation, activate_asset_version
from files.services.output_verification import VerifiedOutputSet


class CandidateConflict(RuntimeError):
    pass


class CandidateNotPublishable(RuntimeError):
    pass


def _locked_attempt(attempt_id):
    attempt = MediaJobAttempt.objects.select_for_update(of=("self",)).get(pk=attempt_id)
    job = MediaIngestionJob.objects.select_for_update().get(pk=attempt.job_id)
    if job.media_id is None:
        raise CandidateConflict("Attempt has no media candidate.")
    media = Media.objects.select_for_update().get(pk=job.media_id)
    return attempt, job, media


def _output_evidence(outputs):
    if not isinstance(outputs, VerifiedOutputSet):
        raise CandidateConflict("Verified output set is required.")
    if not outputs.manifest_key:
        raise CandidateConflict("Candidate manifest is missing.")
    if len({item.evidence.key for item in outputs.outputs}) != len(outputs.outputs):
        raise CandidateConflict("Candidate outputs contain duplicate keys.")
    masters = [item for item in outputs.outputs if item.kind == MediaAsset.Kind.HLS_MASTER]
    if len(masters) != 1 or masters[0].evidence.key != outputs.manifest_key:
        raise CandidateConflict("Candidate must contain one exact master manifest.")
    return outputs


def _candidate_prefix(attempt):
    return f"candidates/{attempt.job.media_id}/{attempt.id}/"


def _ensure_artifacts(attempt, outputs):
    prefix = _candidate_prefix(attempt)
    artifacts = {
        artifact.s3_key: artifact
        for artifact in AttemptArtifact.objects.filter(
            attempt=attempt,
            purpose=ArtifactPurpose.CANDIDATE,
        )
    }
    for item in outputs.outputs:
        evidence = item.evidence
        artifact = artifacts.get(evidence.key)
        if not evidence.key.startswith(prefix) or artifact is None:
            raise CandidateConflict("Candidate output has no exact artifact ledger entry.")
        if any(
            (
                artifact.size_bytes != evidence.size,
                artifact.content_type != evidence.content_type,
                artifact.checksum != evidence.checksum,
            )
        ):
            raise CandidateConflict("Candidate output conflicts with its artifact ledger.")


def _checkpoint_evidence(outputs, version_id=None):
    evidence = {
        "manifest_key": outputs.manifest_key,
        "outputs": [
            {
                "kind": item.kind,
                "key": item.evidence.key,
                "size": item.evidence.size,
                "content_type": item.evidence.content_type,
                "checksum": item.evidence.checksum,
            }
            for item in outputs.outputs
        ],
    }
    if version_id is not None:
        evidence["version_id"] = str(version_id)
    return evidence


def _candidate_assets_complete(version, checkpoint):
    expected = {
        (
            item["kind"],
            item["key"],
            item["size"],
            item["content_type"],
            item["checksum"],
        )
        for item in checkpoint.evidence.get("outputs", [])
    }
    actual = set(
        version.assets.values_list(
            "kind",
            "s3_key",
            "size_bytes",
            "content_type",
            "checksum",
        )
    )
    return expected == actual and bool(expected)


def _existing_candidate(attempt, media, outputs):
    version = MediaAssetVersion.objects.filter(attempt=attempt).first()
    if version is None:
        return None
    if version.media_id != media.id or version.manifest_key != outputs.manifest_key:
        raise CandidateConflict("Existing candidate conflicts with this output set.")
    if version.status not in {
        MediaAssetVersion.Status.CANDIDATE,
        MediaAssetVersion.Status.ACTIVE,
    }:
        raise CandidateConflict("Existing candidate is no longer publishable.")
    return version


def register_candidate(attempt_id, outputs):
    outputs = _output_evidence(outputs)
    with transaction.atomic():
        attempt, job, media = _locked_attempt(attempt_id)
        existing = _existing_candidate(attempt, media, outputs)
        if existing is not None:
            _ensure_artifacts(attempt, outputs)
            return existing
        if job.cancel_requested or job.status == JobStatus.CANCELED:
            raise CandidateNotPublishable("Candidate registration is canceled.")
        if not MediaJobCheckpoint.objects.filter(
            attempt=attempt,
            name="mediaconvert_complete",
            status=CheckpointStatus.COMPLETED,
        ).exists():
            raise CandidateConflict("MediaConvert completion is not verified.")
        _ensure_artifacts(attempt, outputs)
        version = MediaAssetVersion.objects.create(
            media=media,
            attempt=attempt,
            status=MediaAssetVersion.Status.CANDIDATE,
            manifest_key=outputs.manifest_key,
        )
        for item in outputs.outputs:
            MediaAsset.objects.create(
                version=version,
                kind=item.kind,
                s3_key=item.evidence.key,
                checksum=item.evidence.checksum,
                size_bytes=item.evidence.size,
                content_type=item.evidence.content_type,
            )
        MediaJobCheckpoint.objects.update_or_create(
            attempt=attempt,
            name="outputs_verified",
            defaults={
                "status": CheckpointStatus.COMPLETED,
                "evidence": _checkpoint_evidence(outputs, version.id),
                "completed_at": timezone.now(),
            },
        )
        job.stage = "outputs_verified"
        job.save(update_fields=("stage", "updated_at"))
        return version


def attach_subtitle_assets(attempt_id):
    """Add verified YouTube subtitle objects to an existing candidate version."""
    with transaction.atomic():
        attempt, job, media = _locked_attempt(attempt_id)
        version = MediaAssetVersion.objects.select_for_update().filter(attempt=attempt).first()
        checkpoint = MediaJobCheckpoint.objects.filter(
            attempt=attempt,
            name="subtitles",
            status=CheckpointStatus.AVAILABLE,
        ).first()
        if version is None or checkpoint is None:
            return 0
        prefix = _candidate_prefix(attempt) + "subtitles/"
        added = 0
        for language in checkpoint.evidence.get("languages", []):
            key = f"{prefix}{language}.vtt"
            artifact = AttemptArtifact.objects.filter(
                attempt=attempt,
                purpose=ArtifactPurpose.CANDIDATE,
                s3_key=key,
            ).first()
            if artifact is None:
                raise CandidateConflict("Subtitle object has no candidate artifact ledger entry.")
            _, created = MediaAsset.objects.get_or_create(
                version=version,
                s3_key=key,
                defaults={
                    "kind": MediaAsset.Kind.SUBTITLE,
                    "checksum": artifact.checksum,
                    "size_bytes": artifact.size_bytes,
                    "content_type": artifact.content_type,
                },
            )
            added += int(created)
        if added:
            evidence = dict(MediaJobCheckpoint.objects.get(attempt=attempt, name="outputs_verified").evidence)
            for asset in version.assets.filter(kind=MediaAsset.Kind.SUBTITLE):
                if not any(item.get("key") == asset.s3_key for item in evidence.get("outputs", [])):
                    evidence.setdefault("outputs", []).append({"kind": asset.kind, "key": asset.s3_key, "size": asset.size_bytes, "content_type": asset.content_type, "checksum": asset.checksum})
            MediaJobCheckpoint.objects.filter(attempt=attempt, name="outputs_verified").update(evidence=evidence)
        return added


def publish_candidate(attempt_id):
    with transaction.atomic():
        attempt, job, media = _locked_attempt(attempt_id)
        version = MediaAssetVersion.objects.select_for_update().filter(attempt=attempt).first()
        if version is None:
            raise CandidateNotPublishable("Candidate version is not registered.")
        if job.cancel_requested or job.status == JobStatus.CANCELED:
            raise CandidateNotPublishable("Candidate publication is canceled.")
        outputs_checkpoint = MediaJobCheckpoint.objects.filter(
            attempt=attempt,
            name="outputs_verified",
            status=CheckpointStatus.COMPLETED,
        ).first()
        if outputs_checkpoint is None:
            raise CandidateNotPublishable("Candidate outputs are not verified.")
        if version.status == MediaAssetVersion.Status.ACTIVE:
            return media
        if version.status != MediaAssetVersion.Status.CANDIDATE:
            raise CandidateNotPublishable("Candidate version is not publishable.")
        if not MediaAsset.objects.filter(
            version=version,
            kind=MediaAsset.Kind.HLS_MASTER,
            s3_key=version.manifest_key,
        ).exists():
            raise CandidateNotPublishable("Candidate manifest is not registered.")
        if not _candidate_assets_complete(version, outputs_checkpoint):
            raise CandidateNotPublishable("Candidate assets are not complete.")

        try:
            published = activate_asset_version(media.id, version.id)
        except InvalidAssetActivation as error:
            raise CandidateNotPublishable(str(error)) from error
        MediaJobCheckpoint.objects.update_or_create(
            attempt=attempt,
            name="assets_activated",
            defaults={
                "status": CheckpointStatus.COMPLETED,
                "evidence": {
                    "version_id": str(version.id),
                    "manifest_key": version.manifest_key,
                },
                "completed_at": timezone.now(),
            },
        )
        MediaJobCheckpoint.objects.update_or_create(
            attempt=attempt,
            name="media_published",
            defaults={
                "status": CheckpointStatus.COMPLETED,
                "evidence": {
                    "version_id": str(version.id),
                    "media_id": str(media.id),
                },
                "completed_at": timezone.now(),
            },
        )
        now = timezone.now()
        attempt.status = AttemptStatus.COMPLETED
        attempt.completed_at = now
        attempt.save(update_fields=("status", "completed_at", "updated_at"))
        job.status = JobStatus.COMPLETED
        job.stage = "media_published"
        job.progress = 100
        job.save(update_fields=("status", "stage", "progress", "updated_at"))
        return published
