from datetime import timedelta
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError
from django.utils import timezone

from files.models import (
    Media,
    MediaIngestionJob,
    MediaJobAttempt,
    MediaJobCheckpoint,
    MediaJobWarning,
)
from files.services.mediaconvert import ProviderSnapshot
from files.services.processing_polling import poll_attempt
from tests.users.factories import UserFactory


NOW = timezone.now().replace(microsecond=0)


class PollGateway:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def get_job(self, job_id):
        self.calls.append(job_id)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class CallbackPollGateway(PollGateway):
    def __init__(self, result, callback):
        super().__init__(result)
        self.callback = callback

    def get_job(self, job_id):
        result = super().get_job(job_id)
        self.callback()
        return result


def snapshot(status="PROGRESSING", phase="TRANSCODING", percent=25):
    return ProviderSnapshot(
        job_id="mc-job-1",
        status=status,
        phase=phase,
        percent_complete=percent,
        warnings=(),
        output_group_details=(),
    )


@pytest.fixture
def attempt(db):
    owner = UserFactory(is_staff=True, is_superuser=True)
    media = Media.objects.create(
        title="Polling",
        user=owner,
        media_type="video",
        storage_backend="aws",
        processing_status="processing",
        encoding_status="running",
    )
    job = MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot="Polling",
        source_type="upload",
        status="running",
        stage="mediaconvert_submitted",
        progress=20,
        queued_at=NOW - timedelta(minutes=5),
    )
    return MediaJobAttempt.objects.create(
        job=job,
        sequence=1,
        status="running",
        mediaconvert_job_id="mc-job-1",
        provider_status="SUBMITTED",
        submission_intent_at=NOW - timedelta(minutes=5),
        started_at=NOW - timedelta(minutes=5),
    )


@pytest.mark.django_db
def test_changed_provider_evidence_polls_in_ten_seconds(attempt):
    decision = poll_attempt(attempt.id, PollGateway(snapshot()), NOW)

    attempt.refresh_from_db()
    attempt.job.refresh_from_db()
    assert decision.next_delay == 10
    assert decision.terminal is False
    assert attempt.provider_status == "PROGRESSING"
    assert attempt.provider_phase == "TRANSCODING"
    assert attempt.provider_percent_complete == Decimal("25.00")
    assert attempt.provider_last_changed_at == NOW
    assert attempt.provider_unchanged_count == 0
    assert attempt.next_poll_at == NOW + timedelta(seconds=10)
    assert attempt.job.progress == Decimal("25.00")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("prior_count", "expected_delay"),
    ((1, 30), (4, 60)),
)
def test_unchanged_provider_evidence_slows_polling(
    attempt,
    prior_count,
    expected_delay,
):
    MediaJobAttempt.objects.filter(pk=attempt.id).update(
        provider_status="PROGRESSING",
        provider_phase="TRANSCODING",
        provider_percent_complete=25,
        provider_last_changed_at=NOW - timedelta(minutes=1),
        provider_unchanged_count=prior_count,
    )

    decision = poll_attempt(attempt.id, PollGateway(snapshot()), NOW)

    attempt.refresh_from_db()
    assert decision.next_delay == expected_delay
    assert attempt.provider_unchanged_count == prior_count + 1
    assert attempt.next_poll_at == NOW + timedelta(seconds=expected_delay)


@pytest.mark.django_db
def test_missing_or_lower_percentage_does_not_regress_job_progress(attempt):
    MediaJobAttempt.objects.filter(pk=attempt.id).update(
        provider_status="PROGRESSING",
        provider_phase="TRANSCODING",
        provider_percent_complete=25,
    )
    MediaIngestionJob.objects.filter(pk=attempt.job_id).update(progress=40)

    poll_attempt(attempt.id, PollGateway(snapshot(percent=None)), NOW)

    attempt.refresh_from_db()
    attempt.job.refresh_from_db()
    assert attempt.provider_percent_complete == Decimal("25.00")
    assert attempt.job.progress == Decimal("40.00")


@pytest.mark.django_db
def test_stall_warning_is_deduplicated(attempt):
    MediaJobAttempt.objects.filter(pk=attempt.id).update(
        submission_intent_at=NOW - timedelta(minutes=31),
        provider_status="SUBMITTED",
        provider_last_changed_at=NOW - timedelta(minutes=31),
    )
    gateway = PollGateway(snapshot(status="SUBMITTED", phase=None, percent=None))

    poll_attempt(attempt.id, gateway, NOW)
    poll_attempt(attempt.id, gateway, NOW + timedelta(minutes=1))

    warnings = MediaJobWarning.objects.filter(attempt=attempt)
    assert list(warnings.values_list("code", flat=True)) == ["submitted_stalled"]


@pytest.mark.django_db
def test_unchanged_progress_creates_progress_stall_warning(attempt):
    MediaJobAttempt.objects.filter(pk=attempt.id).update(
        provider_status="PROGRESSING",
        provider_phase="TRANSCODING",
        provider_percent_complete=25,
        provider_last_changed_at=NOW - timedelta(minutes=31),
    )

    poll_attempt(attempt.id, PollGateway(snapshot()), NOW)

    assert MediaJobWarning.objects.filter(
        attempt=attempt,
        code="progress_stalled",
    ).exists()


@pytest.mark.django_db
def test_six_hour_timeout_requests_cancel_without_inventing_provider_terminal(attempt):
    MediaJobAttempt.objects.filter(pk=attempt.id).update(
        started_at=NOW - timedelta(hours=6, seconds=1),
    )

    decision = poll_attempt(attempt.id, PollGateway(snapshot()), NOW)

    attempt.refresh_from_db()
    attempt.job.refresh_from_db()
    assert decision.terminal is False
    assert attempt.status == "running"
    assert attempt.job.cancel_requested is True
    assert attempt.job.stage == "cancel_requested"
    assert MediaJobWarning.objects.filter(
        attempt=attempt,
        code="processing_timeout",
    ).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "provider_error",
    (
        ConnectionError("temporary URL https://secret.example"),
        ClientError(
            {
                "Error": {"Code": "ThrottlingException", "Message": "slow down"},
                "ResponseMetadata": {"HTTPStatusCode": 400},
            },
            "GetJob",
        ),
        ClientError(
            {
                "Error": {"Code": "InternalServerException", "Message": "temporary"},
                "ResponseMetadata": {"HTTPStatusCode": 503},
            },
            "GetJob",
        ),
    ),
)
def test_transient_error_preserves_provider_evidence_and_schedules_bounded_retry(
    attempt,
    monkeypatch,
    provider_error,
):
    MediaJobAttempt.objects.filter(pk=attempt.id).update(
        provider_status="PROGRESSING",
        provider_phase="TRANSCODING",
        provider_percent_complete=35,
        checkpoint_evidence={"existing": {"proof": True}},
    )
    monkeypatch.setattr("files.services.processing_polling.random.randint", lambda _a, _b: 1)

    decision = poll_attempt(
        attempt.id,
        PollGateway(provider_error),
        NOW,
    )

    attempt.refresh_from_db()
    assert 1 <= decision.next_delay <= 60
    assert decision.terminal is False
    assert attempt.provider_status == "PROGRESSING"
    assert attempt.provider_phase == "TRANSCODING"
    assert attempt.provider_percent_complete == Decimal("35.00")
    assert attempt.checkpoint_evidence["existing"] == {"proof": True}
    assert attempt.checkpoint_evidence["mediaconvert_poll"]["error_count"] == 1
    assert "secret" not in attempt.diagnostic_error


@pytest.mark.django_db
def test_complete_records_provider_checkpoint_without_completing_pipeline(attempt):
    decision = poll_attempt(
        attempt.id,
        PollGateway(snapshot(status="COMPLETE", phase=None, percent=100)),
        NOW,
    )

    attempt.refresh_from_db()
    attempt.job.refresh_from_db()
    checkpoint = MediaJobCheckpoint.objects.get(
        attempt=attempt,
        name="mediaconvert_complete",
    )
    assert decision.terminal is True
    assert checkpoint.status == "completed"
    assert checkpoint.evidence["job_id"] == "mc-job-1"
    assert checkpoint.evidence["provider_status"] == "COMPLETE"
    assert attempt.status == "running"
    assert attempt.job.status == "running"
    assert attempt.job.stage == "mediaconvert_complete"


@pytest.mark.django_db
def test_error_fails_pipeline_with_safe_message_and_restricted_diagnostic(attempt):
    decision = poll_attempt(
        attempt.id,
        PollGateway(snapshot(status="ERROR", phase=None, percent=None)),
        NOW,
    )

    attempt.refresh_from_db()
    attempt.job.refresh_from_db()
    attempt.job.media.refresh_from_db()
    assert decision.terminal is True
    assert attempt.status == "failed"
    assert attempt.job.status == "failed"
    assert attempt.job.safe_error == "AWS media processing failed. Review the task and retry."
    assert attempt.diagnostic_error == "MediaConvert job mc-job-1 entered ERROR."
    assert attempt.job.media.processing_status == "failed"


@pytest.mark.django_db
def test_cancel_request_is_not_terminal_until_provider_confirms_canceled(attempt):
    MediaIngestionJob.objects.filter(pk=attempt.job_id).update(cancel_requested=True)

    progressing = poll_attempt(attempt.id, PollGateway(snapshot()), NOW)
    canceled = poll_attempt(
        attempt.id,
        PollGateway(snapshot(status="CANCELED", phase=None, percent=None)),
        NOW + timedelta(seconds=10),
    )

    attempt.refresh_from_db()
    attempt.job.refresh_from_db()
    assert progressing.terminal is False
    assert canceled.terminal is True
    assert attempt.status == "canceled"
    assert attempt.job.status == "canceled"
    assert attempt.job.stage == "canceled"


@pytest.mark.django_db
def test_late_provider_response_does_not_overwrite_concurrent_terminal_state(attempt):
    def mark_failed():
        MediaJobAttempt.objects.filter(pk=attempt.id).update(status="failed")

    decision = poll_attempt(
        attempt.id,
        CallbackPollGateway(snapshot(), mark_failed),
        NOW,
    )

    attempt.refresh_from_db()
    assert decision.terminal is True
    assert attempt.status == "failed"
    assert attempt.provider_status == "SUBMITTED"
