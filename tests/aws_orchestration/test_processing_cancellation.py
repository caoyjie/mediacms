from datetime import timedelta

import pytest
from django.utils import timezone

from files.models import Media, MediaIngestionJob, MediaJobAttempt
from files.services.mediaconvert import ProviderSnapshot
from files.services.processing_cancellation import (
    reconcile_cancellation,
    request_attempt_cancel,
)
from tests.users.factories import UserFactory


NOW = timezone.now().replace(microsecond=0)


class CancelGateway:
    def __init__(self, snapshot=None, error=None):
        self.snapshot = snapshot
        self.error = error
        self.cancel_calls = []
        self.get_calls = []

    def cancel_job(self, job_id):
        self.cancel_calls.append(job_id)
        if self.error:
            raise self.error

    def get_job(self, job_id):
        self.get_calls.append(job_id)
        return self.snapshot


def provider(status):
    return ProviderSnapshot(
        job_id="mc-job-1",
        status=status,
        phase=None,
        percent_complete=100 if status == "COMPLETE" else None,
        warnings=(),
        output_group_details=(),
    )


@pytest.fixture
def attempt(db):
    owner = UserFactory(is_staff=True, is_superuser=True)
    media = Media.objects.create(
        title="Cancel",
        user=owner,
        media_type="video",
        storage_backend="aws",
        processing_status="processing",
        encoding_status="running",
    )
    job = MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot=media.title,
        source_type="upload",
        status="running",
        stage="mediaconvert_submitted",
    )
    return MediaJobAttempt.objects.create(
        job=job,
        sequence=1,
        status="running",
        mediaconvert_job_id="mc-job-1",
        provider_status="PROGRESSING",
        started_at=NOW - timedelta(minutes=2),
    )


@pytest.mark.django_db
def test_cancel_request_calls_provider_once_and_persists_intent(attempt):
    gateway = CancelGateway()

    request_attempt_cancel(attempt.id, gateway, now=NOW)
    request_attempt_cancel(attempt.id, gateway, now=NOW + timedelta(seconds=1))

    attempt.refresh_from_db()
    attempt.job.refresh_from_db()
    assert gateway.cancel_calls == ["mc-job-1"]
    assert attempt.job.cancel_requested is True
    assert attempt.job.stage == "cancel_requested"
    assert attempt.checkpoint_evidence["mediaconvert_cancel"]["cancel_call_count"] == 1


@pytest.mark.django_db
def test_cancel_before_submission_is_terminal_without_provider_call(attempt):
    MediaJobAttempt.objects.filter(pk=attempt.id).update(mediaconvert_job_id="")
    gateway = CancelGateway()

    decision = request_attempt_cancel(attempt.id, gateway, now=NOW)

    attempt.refresh_from_db()
    attempt.job.refresh_from_db()
    assert decision.terminal is True
    assert gateway.cancel_calls == []
    assert attempt.status == "canceled"
    assert attempt.job.status == "canceled"


@pytest.mark.django_db
def test_complete_race_after_cancel_never_activates(attempt):
    gateway = CancelGateway(snapshot=provider("COMPLETE"))
    request_attempt_cancel(attempt.id, gateway, now=NOW)

    decision = reconcile_cancellation(attempt.id, gateway, now=NOW + timedelta(seconds=1))

    attempt.refresh_from_db()
    attempt.job.refresh_from_db()
    assert decision.terminal is True
    assert attempt.status == "canceled"
    assert attempt.job.status == "canceled"
    assert attempt.job.stage == "canceled"


@pytest.mark.django_db
def test_error_after_cancel_fails_without_second_cancel_call(attempt):
    gateway = CancelGateway(snapshot=provider("ERROR"))
    request_attempt_cancel(attempt.id, gateway, now=NOW)

    decision = reconcile_cancellation(attempt.id, gateway, now=NOW + timedelta(seconds=1))

    attempt.refresh_from_db()
    attempt.job.refresh_from_db()
    assert decision.terminal is True
    assert gateway.cancel_calls == ["mc-job-1"]
    assert attempt.status == "failed"
    assert attempt.job.status == "failed"


@pytest.mark.django_db
def test_provider_canceled_confirms_cancel_request(attempt):
    gateway = CancelGateway(snapshot=provider("CANCELED"))
    request_attempt_cancel(attempt.id, gateway, now=NOW)

    decision = reconcile_cancellation(attempt.id, gateway, now=NOW + timedelta(seconds=1))

    attempt.refresh_from_db()
    attempt.job.refresh_from_db()
    assert decision.terminal is True
    assert attempt.status == "canceled"
    assert attempt.job.status == "canceled"
    assert attempt.job.stage == "canceled"

