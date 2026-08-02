from dataclasses import replace

import pytest
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
from files.services.asset_publishing import (
    CandidateConflict,
    CandidateNotPublishable,
    register_candidate,
    publish_candidate,
)
from files.services.output_verification import VerifiedOutput, VerifiedOutputSet
from files.services.processing_storage import ObjectEvidence
from tests.users.factories import UserFactory


def output(key, kind, content_type):
    return VerifiedOutput(
        kind,
        ObjectEvidence(
            key=key,
            size=42,
            content_type=content_type,
            checksum=f"sha256:{key}",
        ),
    )


@pytest.fixture
def processing_case(db):
    owner = UserFactory(is_staff=True, is_superuser=True)
    media = Media.objects.create(
        title="Publish",
        user=owner,
        friendly_token="publish-media",
        media_type="video",
        storage_backend="aws",
        processing_status="processing",
        encoding_status="running",
        listable=False,
    )
    job = MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot=media.title,
        source_type="upload",
        status="running",
        stage="outputs_verified",
    )
    attempt = MediaJobAttempt.objects.create(
        job=job,
        sequence=1,
        status="running",
        mediaconvert_job_id="mc-job-1",
    )
    prefix = f"candidates/{media.id}/{attempt.id}/"
    outputs = VerifiedOutputSet(
        manifest_key=f"{prefix}hls/master.m3u8",
        outputs=(
            output(f"{prefix}hls/master.m3u8", "hls_master", "application/vnd.apple.mpegurl"),
            output(f"{prefix}hls/720p.m3u8", "hls_variant", "application/vnd.apple.mpegurl"),
            output(f"{prefix}hls/720p00001.ts", "hls_segment", "video/mp2t"),
            output(f"{prefix}images/poster.jpg", "poster", "image/jpeg"),
        ),
    )
    for item in outputs.outputs:
        AttemptArtifact.objects.create(
            attempt=attempt,
            purpose=ArtifactPurpose.CANDIDATE,
            s3_key=item.evidence.key,
            size_bytes=item.evidence.size,
            content_type=item.evidence.content_type,
            checksum=item.evidence.checksum,
        )
    MediaJobCheckpoint.objects.create(
        attempt=attempt,
        name="mediaconvert_complete",
        status="completed",
        evidence={"job_id": "mc-job-1", "provider_status": "COMPLETE"},
    )
    return media, job, attempt, outputs


def create_active_version(media):
    version = MediaAssetVersion.objects.create(
        media=media,
        status=MediaAssetVersion.Status.ACTIVE,
        manifest_key="candidates/old/active/hls/master.m3u8",
        activated_at=timezone.now(),
    )
    MediaAsset.objects.create(
        version=version,
        kind=MediaAsset.Kind.HLS_MASTER,
        s3_key=version.manifest_key,
        checksum="sha256:old",
        size_bytes=20,
        content_type="application/vnd.apple.mpegurl",
    )
    Media.objects.filter(pk=media.pk).update(active_asset_version=version)
    return version


@pytest.mark.django_db
def test_register_candidate_maps_verified_outputs_and_checkpoint(processing_case):
    media, job, attempt, outputs = processing_case

    version = register_candidate(attempt.id, outputs)

    assert version.status == MediaAssetVersion.Status.CANDIDATE
    assert version.media_id == media.id
    assert version.attempt_id == attempt.id
    assert version.manifest_key == outputs.manifest_key
    assert set(version.assets.values_list("kind", flat=True)) == {
        "hls_master",
        "hls_variant",
        "hls_segment",
        "poster",
    }
    checkpoint = MediaJobCheckpoint.objects.get(
        attempt=attempt,
        name="outputs_verified",
    )
    assert checkpoint.status == "completed"
    assert checkpoint.evidence["manifest_key"] == outputs.manifest_key
    job.refresh_from_db()
    assert job.stage == "outputs_verified"
    media.refresh_from_db()
    assert media.processing_status == "processing"
    assert media.active_asset_version_id is None


@pytest.mark.django_db
def test_register_candidate_is_idempotent_and_does_not_duplicate_assets(processing_case):
    _, _, attempt, outputs = processing_case

    first = register_candidate(attempt.id, outputs)
    second = register_candidate(attempt.id, outputs)

    assert first.id == second.id
    assert MediaAssetVersion.objects.filter(attempt=attempt).count() == 1
    assert MediaAsset.objects.filter(version=first).count() == len(outputs.outputs)


@pytest.mark.django_db
def test_register_candidate_rejects_changed_verified_output(processing_case):
    _, _, attempt, outputs = processing_case
    register_candidate(attempt.id, outputs)
    changed = replace(
        outputs,
        manifest_key=outputs.manifest_key.replace("master", "changed"),
    )

    with pytest.raises(CandidateConflict, match="Candidate"):
        register_candidate(attempt.id, changed)


@pytest.mark.django_db
def test_register_candidate_requires_artifact_ledger(processing_case):
    _, _, attempt, outputs = processing_case
    AttemptArtifact.objects.filter(attempt=attempt, s3_key=outputs.manifest_key).delete()

    with pytest.raises(CandidateConflict, match="artifact"):
        register_candidate(attempt.id, outputs)


@pytest.mark.django_db
def test_register_candidate_requires_mediaconvert_completion(processing_case):
    _, _, attempt, outputs = processing_case
    MediaJobCheckpoint.objects.filter(
        attempt=attempt,
        name="mediaconvert_complete",
    ).delete()

    with pytest.raises(CandidateConflict, match="MediaConvert"):
        register_candidate(attempt.id, outputs)


@pytest.mark.django_db
def test_publish_atomically_activates_candidate_and_updates_compatibility_fields(
    processing_case,
):
    media, job, attempt, outputs = processing_case
    old = create_active_version(media)
    candidate = register_candidate(attempt.id, outputs)

    published = publish_candidate(attempt.id)

    candidate.refresh_from_db()
    old.refresh_from_db()
    job.refresh_from_db()
    attempt.refresh_from_db()
    published.refresh_from_db()
    assert published.active_asset_version_id == candidate.id
    assert candidate.status == MediaAssetVersion.Status.ACTIVE
    assert old.status == MediaAssetVersion.Status.RETIRED
    assert published.processing_status == "ready"
    assert published.encoding_status == "success"
    assert published.hls_file == outputs.manifest_key
    assert job.stage == "media_published"
    assert MediaJobCheckpoint.objects.filter(
        attempt=attempt,
        name="assets_activated",
        status="completed",
    ).exists()
    assert MediaJobCheckpoint.objects.filter(
        attempt=attempt,
        name="media_published",
        status="completed",
    ).exists()


@pytest.mark.django_db
def test_publish_rejects_cancel_requested_and_preserves_previous_active(processing_case):
    media, job, attempt, outputs = processing_case
    old = create_active_version(media)
    candidate = register_candidate(attempt.id, outputs)
    MediaIngestionJob.objects.filter(pk=job.pk).update(cancel_requested=True)

    with pytest.raises(CandidateNotPublishable, match="cancel"):
        publish_candidate(attempt.id)

    candidate.refresh_from_db()
    old.refresh_from_db()
    media.refresh_from_db()
    assert candidate.status == MediaAssetVersion.Status.CANDIDATE
    assert old.status == MediaAssetVersion.Status.ACTIVE
    assert media.active_asset_version_id == old.id
    assert media.processing_status == "processing"


@pytest.mark.django_db
def test_publish_rechecks_every_verified_asset_before_activation(processing_case):
    media, _, attempt, outputs = processing_case
    old = create_active_version(media)
    candidate = register_candidate(attempt.id, outputs)
    MediaAsset.objects.filter(
        version=candidate,
        kind=MediaAsset.Kind.HLS_SEGMENT,
    ).delete()

    with pytest.raises(CandidateNotPublishable, match="complete"):
        publish_candidate(attempt.id)

    media.refresh_from_db()
    old.refresh_from_db()
    candidate.refresh_from_db()
    assert media.active_asset_version_id == old.id
    assert old.status == MediaAssetVersion.Status.ACTIVE
    assert candidate.status == MediaAssetVersion.Status.CANDIDATE


@pytest.mark.django_db
def test_publish_transaction_failure_keeps_old_active_and_candidate_state(
    processing_case,
    monkeypatch,
):
    media, _, attempt, outputs = processing_case
    old = create_active_version(media)
    candidate = register_candidate(attempt.id, outputs)
    original = MediaJobCheckpoint.objects.update_or_create

    def fail_after_activation(*args, **kwargs):
        if kwargs.get("defaults", {}).get("status") == "completed":
            raise RuntimeError("checkpoint write failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(MediaJobCheckpoint.objects, "update_or_create", fail_after_activation)

    with pytest.raises(RuntimeError, match="checkpoint"):
        publish_candidate(attempt.id)

    media.refresh_from_db()
    old.refresh_from_db()
    candidate.refresh_from_db()
    assert media.active_asset_version_id == old.id
    assert media.processing_status == "processing"
    assert old.status == MediaAssetVersion.Status.ACTIVE
    assert candidate.status == MediaAssetVersion.Status.CANDIDATE
