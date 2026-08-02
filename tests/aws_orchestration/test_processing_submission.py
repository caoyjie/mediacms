from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import close_old_connections
from django.utils import timezone

from files.models import (
    ArtifactPurpose,
    AttemptArtifact,
    Media,
    MediaIngestionJob,
    MediaJobAttempt,
    MediaJobCheckpoint,
)
from files.services.media_probe import SourceFacts
from files.services.mediaconvert import build_job_request
from files.services.processing_submission import (
    SubmissionIntentConflict,
    SubmissionOutcomeUnknown,
    prepare_submission,
    reconcile_unknown_submission,
    submit_prepared,
)
from tests.aws_orchestration.test_mediaconvert_gateway import reconciliation_job
from tests.users.factories import UserFactory


BUCKET = "mediacms-123456789012-us-east-1"
ROLE = "arn:aws:iam::123456789012:role/mediacms-dev-mediaconvert"
FACTS = SourceFacts("video", 20.0, 1280, 720, True)


@pytest.fixture(autouse=True)
def submission_settings(settings):
    settings.AWS_MEDIA_BUCKET = BUCKET
    settings.AWS_MEDIACONVERT_ROLE_ARN = ROLE
    settings.AWS_MEDIACONVERT_VIDEO_TEMPLATE = "mediacms-dev-video-hls-v1"
    settings.AWS_MEDIACONVERT_AUDIO_TEMPLATE = "mediacms-dev-audio-hls-v1"
    settings.AWS_MEDIACONVERT_TEMPLATE_VERSION = "h264-hls-qvbr-v1"
    settings.AWS_ENVIRONMENT = "dev"
    settings.AWS_MEDIACONVERT_RECONCILIATION_LIMIT = 3
    settings.AWS_MEDIACONVERT_TOKEN_WINDOW_SECONDS = 60


@pytest.fixture
def attempt(db):
    owner = UserFactory(is_staff=True, is_superuser=True)
    media = Media.objects.create(
        title="Submission",
        user=owner,
        media_type="video",
        storage_backend="aws",
        processing_status="processing",
        encoding_status="running",
    )
    job = MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot="Submission",
        source_type="upload",
        status="running",
        stage="source_verified",
    )
    attempt = MediaJobAttempt.objects.create(job=job, sequence=1, status="running")
    AttemptArtifact.objects.create(
        attempt=attempt,
        purpose=ArtifactPurpose.ORIGINAL,
        s3_key=f"originals/{media.id}/{attempt.id}/source.mp4",
        size_bytes=42,
        content_type="video/mp4",
        checksum="source-sha256",
    )
    return attempt


class SubmissionGateway:
    def __init__(self):
        self.create_calls = []
        self.list_calls = 0
        self.create_results = []
        self.jobs = ()

    def create_job(self, request):
        self.create_calls.append(request)
        result = self.create_results.pop(0) if self.create_results else "mc-job-1"
        if isinstance(result, BaseException):
            raise result
        return result

    def list_jobs(self):
        self.list_calls += 1
        return self.jobs


@pytest.mark.django_db
def test_prepare_persists_immutable_intent_before_any_create(attempt):
    gateway = SubmissionGateway()

    prepared = prepare_submission(attempt.id, FACTS)

    attempt.refresh_from_db()
    checkpoint = MediaJobCheckpoint.objects.get(
        attempt=attempt,
        name="mediaconvert_submitting",
    )
    assert prepared.owner is True
    assert gateway.create_calls == []
    assert attempt.template_name == "mediacms-dev-video-hls-v1"
    assert attempt.template_version == "h264-hls-qvbr-v1"
    assert len(attempt.client_request_token) == 64
    assert attempt.submission_intent_at is not None
    assert checkpoint.status == "completed"
    assert checkpoint.evidence == {
        "template_name": "mediacms-dev-video-hls-v1",
        "template_version": "h264-hls-qvbr-v1",
        "client_request_token": attempt.client_request_token,
        "input_key": f"originals/{attempt.job.media_id}/{attempt.id}/source.mp4",
        "input_checksum": "source-sha256",
        "candidate_prefix": f"candidates/{attempt.job.media_id}/{attempt.id}/",
        "source_facts": {
            "media_type": "video",
            "duration_seconds": 20.0,
            "width": 1280,
            "height": 720,
            "has_audio": True,
        },
        "request_fingerprint": prepared.request_fingerprint,
    }
    assert prepare_submission(attempt.id, FACTS).owner is False


@pytest.mark.django_db
def test_existing_provider_job_id_prevents_create(attempt):
    prepare_submission(attempt.id, FACTS)
    MediaJobAttempt.objects.filter(pk=attempt.id).update(mediaconvert_job_id="existing-job")
    gateway = SubmissionGateway()

    assert submit_prepared(attempt.id, gateway) == "existing-job"
    assert gateway.create_calls == []


@pytest.mark.django_db
def test_changed_input_checksum_or_template_conflicts_with_durable_intent(
    attempt,
    settings,
):
    prepare_submission(attempt.id, FACTS)
    AttemptArtifact.objects.filter(
        attempt=attempt,
        purpose=ArtifactPurpose.ORIGINAL,
    ).update(checksum="changed")
    with pytest.raises(SubmissionIntentConflict, match="intent"):
        prepare_submission(attempt.id, FACTS)

    AttemptArtifact.objects.filter(
        attempt=attempt,
        purpose=ArtifactPurpose.ORIGINAL,
    ).update(checksum="source-sha256")
    settings.AWS_MEDIACONVERT_VIDEO_TEMPLATE = "mediacms-dev-video-hls-v2"
    with pytest.raises(SubmissionIntentConflict, match="intent"):
        prepare_submission(attempt.id, FACTS)


@pytest.mark.django_db
def test_timeout_reuses_same_token_once_then_list_jobs_recovers_exact_job(attempt):
    prepared = prepare_submission(attempt.id, FACTS)
    gateway = SubmissionGateway()
    gateway.create_results = [
        TimeoutError("response lost after create"),
        TimeoutError("idempotent response also lost"),
    ]

    with pytest.raises(SubmissionOutcomeUnknown):
        submit_prepared(attempt.id, gateway)

    attempt.refresh_from_db()
    first_request = gateway.create_calls[0]
    intent_at = attempt.submission_intent_at
    with pytest.raises(SubmissionOutcomeUnknown):
        reconcile_unknown_submission(
            attempt.id,
            gateway,
            intent_at + timedelta(seconds=30),
        )
    assert len(gateway.create_calls) == 2
    assert gateway.create_calls[1]["ClientRequestToken"] == first_request["ClientRequestToken"]
    assert gateway.list_calls == 0

    gateway.jobs = (reconciliation_job(prepared.request, "mc-recovered"),)
    assert (
        reconcile_unknown_submission(
            attempt.id,
            gateway,
            intent_at + timedelta(seconds=31),
        )
        == "mc-recovered"
    )

    attempt.refresh_from_db()
    assert attempt.mediaconvert_job_id == "mc-recovered"
    assert len(gateway.create_calls) == 2
    assert gateway.list_calls == 1
    submitted = MediaJobCheckpoint.objects.get(
        attempt=attempt,
        name="mediaconvert_submitted",
    )
    assert submitted.evidence == {
        "job_id": "mc-recovered",
        "template_name": "mediacms-dev-video-hls-v1",
        "template_version": "h264-hls-qvbr-v1",
        "client_request_token": first_request["ClientRequestToken"],
    }


@pytest.mark.django_db
def test_successful_create_persists_job_id_once(attempt):
    prepare_submission(attempt.id, FACTS)
    gateway = SubmissionGateway()
    gateway.create_results = ["mc-created"]

    assert submit_prepared(attempt.id, gateway) == "mc-created"
    assert submit_prepared(attempt.id, gateway) == "mc-created"
    assert len(gateway.create_calls) == 1


@pytest.mark.django_db
def test_after_token_window_reconciliation_never_calls_create(attempt):
    prepare_submission(attempt.id, FACTS)
    gateway = SubmissionGateway()
    gateway.create_results = [TimeoutError("response lost")]
    with pytest.raises(SubmissionOutcomeUnknown):
        submit_prepared(attempt.id, gateway)
    attempt.refresh_from_db()

    reconcile_unknown_submission(
        attempt.id,
        gateway,
        attempt.submission_intent_at + timedelta(seconds=61),
    )

    assert len(gateway.create_calls) == 1
    assert gateway.list_calls == 1


@pytest.mark.django_db
def test_no_proof_becomes_action_required_after_bounded_reconciliation(attempt):
    prepare_submission(attempt.id, FACTS)
    gateway = SubmissionGateway()
    gateway.create_results = [TimeoutError("response lost")]
    with pytest.raises(SubmissionOutcomeUnknown):
        submit_prepared(attempt.id, gateway)
    attempt.refresh_from_db()
    start = attempt.submission_intent_at + timedelta(seconds=61)

    assert reconcile_unknown_submission(attempt.id, gateway, start) is None
    assert reconcile_unknown_submission(attempt.id, gateway, start + timedelta(seconds=30)) is None
    assert reconcile_unknown_submission(attempt.id, gateway, start + timedelta(seconds=60)) is None

    attempt.refresh_from_db()
    attempt.job.refresh_from_db()
    attempt.job.media.refresh_from_db()
    assert attempt.status == "failed"
    assert attempt.job.status == "failed"
    assert attempt.job.stage == "action_required"
    assert attempt.job.media.processing_status == "failed"
    assert attempt.job.safe_error == "Processing needs administrator review before retry."
    assert "response lost" not in attempt.diagnostic_error
    assert len(gateway.create_calls) == 1
    assert gateway.list_calls == 3


@pytest.mark.django_db
def test_ambiguous_jobs_never_store_arbitrary_job_id(attempt):
    prepared = prepare_submission(attempt.id, FACTS)
    gateway = SubmissionGateway()
    gateway.create_results = [TimeoutError("response lost")]
    with pytest.raises(SubmissionOutcomeUnknown):
        submit_prepared(attempt.id, gateway)
    attempt.refresh_from_db()
    gateway.jobs = (
        reconciliation_job(prepared.request, "mc-one"),
        reconciliation_job(prepared.request, "mc-two"),
    )
    start = attempt.submission_intent_at + timedelta(seconds=61)

    for offset in (0, 30, 60):
        reconcile_unknown_submission(
            attempt.id,
            gateway,
            start + timedelta(seconds=offset),
        )

    attempt.refresh_from_db()
    assert attempt.mediaconvert_job_id == ""
    assert attempt.status == "failed"
    assert len(gateway.create_calls) == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_prepare_has_exactly_one_intent_owner(settings):
    owner = UserFactory(is_staff=True, is_superuser=True)
    media = Media.objects.create(
        title="Concurrent",
        user=owner,
        media_type="video",
        storage_backend="aws",
        processing_status="processing",
        encoding_status="running",
    )
    job = MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot="Concurrent",
        source_type="upload",
        status="running",
    )
    attempt = MediaJobAttempt.objects.create(job=job, sequence=1, status="running")
    AttemptArtifact.objects.create(
        attempt=attempt,
        purpose=ArtifactPurpose.ORIGINAL,
        s3_key=f"originals/{media.id}/{attempt.id}/source.mp4",
        size_bytes=42,
        content_type="video/mp4",
        checksum="source-sha256",
    )

    def run_prepare():
        close_old_connections()
        try:
            return prepare_submission(attempt.id, FACTS).owner
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        owners = list(executor.map(lambda _: run_prepare(), range(2)))

    assert sorted(owners) == [False, True]
    assert MediaJobCheckpoint.objects.filter(
        attempt=attempt,
        name="mediaconvert_submitting",
    ).count() == 1
