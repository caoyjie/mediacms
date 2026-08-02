from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from files.models import Media, MediaIngestionJob, MediaJobAttempt
from files.models.domain import encoding_status_for
from files.models.ingestion import AttemptStatus, JobStatus


@dataclass(frozen=True, slots=True)
class CancellationDecision:
    terminal: bool
    provider_status: str = ""


def _save_cancel_state(attempt, state, *, now, provider_status=None, error=None):
    root = dict(attempt.checkpoint_evidence)
    root["mediaconvert_cancel"] = state
    attempt.checkpoint_evidence = root
    fields = ["checkpoint_evidence", "updated_at"]
    if provider_status is not None:
        attempt.provider_status = provider_status
        fields.append("provider_status")
    if error is not None:
        attempt.diagnostic_error = "Temporary MediaConvert cancellation error."
        fields.extend(("diagnostic_error",))
    attempt.save(update_fields=tuple(fields))


def _confirm_canceled(attempt, now):
    attempt.status = AttemptStatus.CANCELED
    attempt.completed_at = now
    MediaIngestionJob.objects.filter(pk=attempt.job_id).update(
        status=JobStatus.CANCELED,
        stage="canceled",
        safe_error="Processing was canceled.",
    )


def _fail_canceled_provider(attempt, now):
    attempt.status = AttemptStatus.FAILED
    attempt.completed_at = now
    attempt.diagnostic_error = f"MediaConvert job {attempt.mediaconvert_job_id} entered ERROR."
    MediaIngestionJob.objects.filter(pk=attempt.job_id).update(
        status=JobStatus.FAILED,
        stage="failed",
        safe_error="AWS media processing failed. Review the task and retry.",
    )
    if attempt.job.media_id is not None:
        Media.objects.filter(pk=attempt.job.media_id).update(
            processing_status="failed",
            encoding_status=encoding_status_for("failed"),
        )


def request_attempt_cancel(attempt_id, gateway, *, now=None):
    now = now or timezone.now()
    should_call = False
    job_id = None
    with transaction.atomic():
        attempt = (
            MediaJobAttempt.objects.select_for_update(of=("self",))
            .select_related("job")
            .get(pk=attempt_id)
        )
        if attempt.status in {AttemptStatus.FAILED, AttemptStatus.CANCELED, AttemptStatus.COMPLETED}:
            return CancellationDecision(True, attempt.provider_status)
        job = MediaIngestionJob.objects.select_for_update().get(pk=attempt.job_id)
        job.cancel_requested = True
        job.stage = "cancel_requested"
        job.save(update_fields=("cancel_requested", "stage", "updated_at"))
        root = dict(attempt.checkpoint_evidence)
        state = dict(root.get("mediaconvert_cancel", {}))
        state.setdefault("requested_at", now.isoformat())
        state.setdefault("cancel_call_count", 0)
        if not attempt.mediaconvert_job_id:
            _confirm_canceled(attempt, now)
            attempt.save(update_fields=("status", "completed_at", "updated_at"))
            return CancellationDecision(True, "")
        if state["cancel_call_count"] == 0:
            state["cancel_call_count"] = 1
            should_call = True
            job_id = attempt.mediaconvert_job_id
        _save_cancel_state(attempt, state, now=now)
    if should_call:
        try:
            gateway.cancel_job(job_id)
        except Exception:
            with transaction.atomic():
                attempt = MediaJobAttempt.objects.select_for_update().get(pk=attempt_id)
                root = dict(attempt.checkpoint_evidence)
                state = dict(root.get("mediaconvert_cancel", {}))
                state["last_error"] = "provider_cancel_failed"
                _save_cancel_state(attempt, state, now=now, error=True)
    return CancellationDecision(False, "")


def reconcile_cancellation(attempt_id, gateway, *, now=None):
    now = now or timezone.now()
    attempt = MediaJobAttempt.objects.get(pk=attempt_id)
    if attempt.status in {AttemptStatus.FAILED, AttemptStatus.CANCELED, AttemptStatus.COMPLETED}:
        return CancellationDecision(True, attempt.provider_status)
    if not attempt.mediaconvert_job_id:
        return request_attempt_cancel(attempt_id, gateway, now=now)
    snapshot = gateway.get_job(attempt.mediaconvert_job_id)
    with transaction.atomic():
        attempt = (
            MediaJobAttempt.objects.select_for_update(of=("self",))
            .select_related("job")
            .get(pk=attempt_id)
        )
        if attempt.status in {AttemptStatus.FAILED, AttemptStatus.CANCELED, AttemptStatus.COMPLETED}:
            return CancellationDecision(True, attempt.provider_status)
        attempt.provider_status = snapshot.status
        attempt.provider_phase = snapshot.phase or ""
        if snapshot.status == "CANCELED":
            _confirm_canceled(attempt, now)
            decision = CancellationDecision(True, snapshot.status)
        elif snapshot.status == "ERROR":
            _fail_canceled_provider(attempt, now)
            decision = CancellationDecision(True, snapshot.status)
        elif snapshot.status == "COMPLETE":
            attempt.diagnostic_error = "MediaConvert completed after cancellation; outputs were not activated."
            _confirm_canceled(attempt, now)
            decision = CancellationDecision(True, snapshot.status)
        else:
            decision = CancellationDecision(False, snapshot.status)
        attempt.save(update_fields=("status", "provider_status", "provider_phase", "diagnostic_error", "completed_at", "updated_at"))
        return decision
