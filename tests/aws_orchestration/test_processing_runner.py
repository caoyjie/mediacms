from dataclasses import dataclass
from datetime import timedelta

import pytest
from django.utils import timezone

from files.models import Media, MediaIngestionJob, MediaJobAttempt, MediaJobCheckpoint
from files.services.media_probe import SourceFacts
from files.services.processing_runner import run_processing_tick
from files.services.mediaconvert import ProviderSnapshot
from files.services.processing_polling import PollDecision
from tests.users.factories import UserFactory


NOW = timezone.now().replace(microsecond=0)


@dataclass
class CallLog:
    actions: list


class Gateway:
    def __init__(self):
        self.calls = []

    def get_job(self, job_id):
        return ProviderSnapshot(job_id, "COMPLETE", None, 100, (), ())


@pytest.fixture
def queued_job(db):
    owner = UserFactory(is_staff=True, is_superuser=True)
    media = Media.objects.create(
        title="Runner",
        user=owner,
        media_type="video",
        storage_backend="aws",
        processing_status="queued",
        encoding_status="pending",
    )
    job = MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot=media.title,
        source_type="upload",
        status="queued",
        stage="source_verified",
    )
    attempt = MediaJobAttempt.objects.create(job=job, sequence=1, status="queued")
    MediaJobCheckpoint.objects.create(
        attempt=attempt,
        name="source_verified",
        status="completed",
        evidence={
            "s3_key": "originals/media/attempt/source.mp4",
            "size": 10,
            "content_type": "video/mp4",
            "checksum": "sha256:source",
        },
    )
    return job, attempt


@pytest.mark.django_db
def test_tick_performs_probe_as_one_action_and_schedules_next(monkeypatch, queued_job):
    job, attempt = queued_job
    media_gateway = Gateway()
    storage_gateway = Gateway()
    facts = SourceFacts("video", 20.0, 640, 360, True)
    calls = []
    monkeypatch.setattr("files.services.processing_runner.MediaConvertGateway", lambda: media_gateway)
    monkeypatch.setattr("files.services.processing_runner.ProcessingStorageGateway", lambda: storage_gateway)
    monkeypatch.setattr("files.services.processing_runner.probe_source", lambda uri, gateway: calls.append((uri, gateway)) or facts)
    monkeypatch.setattr("files.services.processing_runner.settings.AWS_PROCESSING_LEASE_SECONDS", 120, raising=False)
    monkeypatch.setattr("files.services.processing_runner.settings.AWS_MEDIA_BUCKET", "mediacms-test", raising=False)

    result = run_processing_tick("runner-owner", NOW)

    assert result.action == "probe"
    assert result.scheduled_delay == 0
    assert calls == [("s3://mediacms-test/originals/media/attempt/source.mp4", media_gateway)]
    assert MediaJobCheckpoint.objects.filter(attempt=attempt, name="source_probed", status="completed").exists()
    job.refresh_from_db()
    assert job.stage == "source_probed"


@pytest.mark.django_db
def test_tick_does_not_run_two_actions_for_prepared_submission(monkeypatch, queued_job):
    job, attempt = queued_job
    MediaJobCheckpoint.objects.create(
        attempt=attempt,
        name="source_probed",
        status="completed",
        evidence={"media_type": "video", "duration_seconds": 20.0, "width": 640, "height": 360, "has_audio": True},
    )
    calls = []
    monkeypatch.setattr("files.services.processing_runner.MediaConvertGateway", lambda: Gateway())
    monkeypatch.setattr("files.services.processing_runner.ProcessingStorageGateway", lambda: Gateway())
    monkeypatch.setattr("files.services.processing_runner.prepare_submission", lambda attempt_id, facts: calls.append(("prepare", attempt_id, facts)) or None)

    result = run_processing_tick("runner-owner", NOW)

    assert result.action == "prepare_submission"
    assert len(calls) == 1
    assert calls[0][0] == "prepare"


@pytest.mark.django_db
def test_reconciler_schedules_only_due_running_work(monkeypatch, queued_job):
    from files.services.processing_runner import reconcile_processing

    job, attempt = queued_job
    MediaIngestionJob.objects.filter(pk=job.pk).update(queued_at=NOW - timedelta(seconds=1))
    queued = MediaIngestionJob.objects.create(
        media=job.media,
        media_title_snapshot="later",
        source_type="upload",
        status="queued",
        queued_at=NOW + timedelta(hours=1),
    )
    called = []
    class FakeTask:
        def apply_async(self, **kwargs):
            called.append(kwargs)

    monkeypatch.setattr("files.services.processing_runner.aws_processing_tick", FakeTask())

    result = reconcile_processing(NOW)

    assert result.wakeups == 1
    assert [item["args"] for item in called] == [(str(job.id),)]
    assert queued.id != job.id


@pytest.mark.django_db
def test_tick_submits_prepared_intent_as_one_action(monkeypatch, queued_job):
    job, attempt = queued_job
    MediaJobCheckpoint.objects.create(
        attempt=attempt,
        name="source_probed",
        status="completed",
        evidence={"media_type": "video", "duration_seconds": 20.0, "width": 640, "height": 360, "has_audio": True},
    )
    MediaJobCheckpoint.objects.create(attempt=attempt, name="mediaconvert_submitting", status="completed")
    calls = []
    monkeypatch.setattr("files.services.processing_runner.MediaConvertGateway", Gateway)
    monkeypatch.setattr("files.services.processing_runner.ProcessingStorageGateway", Gateway)
    monkeypatch.setattr("files.services.processing_runner.submit_prepared", lambda attempt_id, gateway: calls.append(attempt_id))

    result = run_processing_tick("runner-owner", NOW)

    assert result.action == "submit"
    assert calls == [attempt.id]


@pytest.mark.django_db
def test_tick_turns_unknown_create_result_into_reconciliation_action(monkeypatch, queued_job):
    from files.services.processing_submission import SubmissionOutcomeUnknown

    job, attempt = queued_job
    MediaJobCheckpoint.objects.create(
        attempt=attempt,
        name="source_probed",
        status="completed",
        evidence={"media_type": "video", "duration_seconds": 20.0, "width": 640, "height": 360, "has_audio": True},
    )
    MediaJobCheckpoint.objects.create(attempt=attempt, name="mediaconvert_submitting", status="completed")
    MediaJobAttempt.objects.filter(pk=attempt.id).update(
        checkpoint_evidence={"mediaconvert_submission": {"create_attempts": 1}}
    )
    monkeypatch.setattr("files.services.processing_runner.MediaConvertGateway", Gateway)
    monkeypatch.setattr("files.services.processing_runner.ProcessingStorageGateway", Gateway)
    monkeypatch.setattr(
        "files.services.processing_runner.reconcile_unknown_submission",
        lambda attempt_id, gateway, now: (_ for _ in ()).throw(SubmissionOutcomeUnknown("unknown")),
    )

    result = run_processing_tick("runner-owner", NOW)

    assert result.action == "reconcile_submission"


@pytest.mark.django_db
def test_tick_polls_due_provider_as_one_action(monkeypatch, queued_job):
    job, attempt = queued_job
    MediaJobAttempt.objects.filter(pk=attempt.id).update(
        mediaconvert_job_id="mc-job-1", provider_status="PROGRESSING", next_poll_at=NOW
    )
    MediaJobCheckpoint.objects.create(
        attempt=attempt,
        name="source_probed",
        status="completed",
        evidence={"media_type": "video", "duration_seconds": 20.0, "width": 640, "height": 360, "has_audio": True},
    )
    MediaJobCheckpoint.objects.create(attempt=attempt, name="mediaconvert_submitting", status="completed")
    calls = []
    monkeypatch.setattr("files.services.processing_runner.MediaConvertGateway", Gateway)
    monkeypatch.setattr("files.services.processing_runner.ProcessingStorageGateway", Gateway)
    monkeypatch.setattr("files.services.processing_runner.poll_attempt", lambda attempt_id, gateway, now: calls.append(attempt_id) or PollDecision(10, False))

    result = run_processing_tick("runner-owner", NOW)

    assert result.action == "poll"
    assert calls == [attempt.id]


@pytest.mark.django_db
def test_tick_verifies_complete_outputs_as_one_action(monkeypatch, queued_job):
    job, attempt = queued_job
    MediaJobAttempt.objects.filter(pk=attempt.id).update(mediaconvert_job_id="mc-job-1", provider_status="COMPLETE")
    for name in ("source_probed", "mediaconvert_submitting", "mediaconvert_complete"):
        MediaJobCheckpoint.objects.create(attempt=attempt, name=name, status="completed", evidence={})
    snapshot = ProviderSnapshot("mc-job-1", "COMPLETE", None, 100, (), ())
    calls = []
    gateway = Gateway()
    monkeypatch.setattr("files.services.processing_runner.MediaConvertGateway", lambda: gateway)
    monkeypatch.setattr("files.services.processing_runner.ProcessingStorageGateway", Gateway)
    monkeypatch.setattr("files.services.processing_runner.verify_mediaconvert_outputs", lambda attempt_id, returned, storage: calls.append((attempt_id, returned)) or object())
    monkeypatch.setattr("files.services.processing_runner.register_candidate", lambda attempt_id, outputs: calls.append(("register", attempt_id)))

    result = run_processing_tick("runner-owner", NOW)

    assert result.action == "verify_outputs"
    assert calls[0] == (attempt.id, snapshot)


@pytest.mark.django_db
def test_tick_publishes_then_cleans_as_separate_actions(monkeypatch, queued_job):
    job, attempt = queued_job
    MediaJobAttempt.objects.filter(pk=attempt.id).update(mediaconvert_job_id="mc-job-1")
    for name in ("source_probed", "mediaconvert_submitting", "mediaconvert_complete", "outputs_verified"):
        MediaJobCheckpoint.objects.create(attempt=attempt, name=name, status="completed", evidence={})
    monkeypatch.setattr("files.services.processing_runner.MediaConvertGateway", Gateway)
    monkeypatch.setattr("files.services.processing_runner.ProcessingStorageGateway", Gateway)
    publish_calls = []
    monkeypatch.setattr("files.services.processing_runner.publish_candidate", lambda attempt_id: publish_calls.append(attempt_id))

    first = run_processing_tick("runner-owner", NOW)

    assert first.action == "publish"
    assert publish_calls == [attempt.id]
