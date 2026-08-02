import random
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from botocore.exceptions import BotoCoreError, ClientError
from django.db import transaction

from files.models import (
    Media,
    MediaIngestionJob,
    MediaJobAttempt,
    MediaJobCheckpoint,
    MediaJobWarning,
)
from files.models.domain import encoding_status_for
from files.models.ingestion import AttemptStatus, CheckpointStatus, JobStatus
from files.services.mediaconvert import InvalidMediaConvertEvidence


@dataclass(frozen=True, slots=True)
class PollDecision:
    next_delay: int | None
    terminal: bool


_TERMINAL_ATTEMPT_STATUSES = {
    AttemptStatus.FAILED,
    AttemptStatus.CANCELED,
    AttemptStatus.COMPLETED,
}
_STALL_AFTER = timedelta(minutes=30)
_TIMEOUT_AFTER = timedelta(hours=6)


def _warning(attempt, code, message):
    MediaJobWarning.objects.get_or_create(
        attempt=attempt,
        code=code,
        defaults={"message": message},
    )


def _poll_state(attempt):
    root = dict(attempt.checkpoint_evidence)
    state = dict(root.get("mediaconvert_poll", {}))
    return root, state


def _save_poll_state(attempt, root, state):
    root["mediaconvert_poll"] = state
    attempt.checkpoint_evidence = root


def _retry_after_error(attempt_id, now):
    with transaction.atomic():
        attempt = MediaJobAttempt.objects.select_for_update().get(pk=attempt_id)
        root, state = _poll_state(attempt)
        error_count = min(int(state.get("error_count", 0)) + 1, 10)
        state["error_count"] = error_count
        state["last_error_kind"] = "temporary_provider_error"
        base = min(5 * (2 ** (error_count - 1)), 60)
        delay = min(base + random.randint(0, min(5, base)), 60)
        _save_poll_state(attempt, root, state)
        attempt.diagnostic_error = "Temporary MediaConvert polling error."
        attempt.next_poll_at = now + timedelta(seconds=delay)
        attempt.save(
            update_fields=(
                "checkpoint_evidence",
                "diagnostic_error",
                "next_poll_at",
                "updated_at",
            )
        )
    return PollDecision(delay, False)


def _is_transient(error):
    if isinstance(error, (TimeoutError, ConnectionError, BotoCoreError)):
        return True
    if isinstance(error, ClientError):
        response = error.response
        code = str(response.get("Error", {}).get("Code", "")).lower()
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return status is not None and status >= 500 or "throttl" in code
    return False


def _changed(attempt, snapshot):
    if attempt.provider_status != snapshot.status:
        return True
    if attempt.provider_phase != (snapshot.phase or ""):
        return True
    if snapshot.percent_complete is None:
        return False
    return attempt.provider_percent_complete != Decimal(
        str(snapshot.percent_complete)
    )


def _delay_for(unchanged_count):
    if unchanged_count >= 5:
        return 60
    if unchanged_count >= 2:
        return 30
    return 10


def _record_stall_or_timeout(attempt, now):
    started_at = attempt.started_at or attempt.submission_intent_at or attempt.created_at
    if now - started_at > _TIMEOUT_AFTER:
        _warning(
            attempt,
            "processing_timeout",
            "Media processing exceeded six hours and cancellation was requested.",
        )
        MediaIngestionJob.objects.filter(pk=attempt.job_id).update(
            cancel_requested=True,
            stage="cancel_requested",
        )
        return
    last_change = attempt.provider_last_changed_at or started_at
    if now - last_change <= _STALL_AFTER:
        return
    if attempt.provider_status == "SUBMITTED":
        _warning(
            attempt,
            "submitted_stalled",
            "MediaConvert has remained submitted for more than 30 minutes.",
        )
    elif attempt.provider_status == "PROGRESSING":
        _warning(
            attempt,
            "progress_stalled",
            "MediaConvert progress has not changed for more than 30 minutes.",
        )


def _complete_provider(attempt, snapshot, now):
    MediaJobCheckpoint.objects.update_or_create(
        attempt=attempt,
        name="mediaconvert_complete",
        defaults={
            "status": CheckpointStatus.COMPLETED,
            "evidence": {
                "job_id": snapshot.job_id,
                "provider_status": snapshot.status,
                "warnings": list(snapshot.warnings),
                "output_group_details": list(snapshot.output_group_details),
            },
            "completed_at": now,
        },
    )
    MediaIngestionJob.objects.filter(pk=attempt.job_id).update(
        stage="mediaconvert_complete",
        progress=100,
    )


def _fail_provider(attempt, now):
    safe_error = "AWS media processing failed. Review the task and retry."
    attempt.status = AttemptStatus.FAILED
    attempt.completed_at = now
    attempt.diagnostic_error = (
        f"MediaConvert job {attempt.mediaconvert_job_id} entered ERROR."
    )
    MediaIngestionJob.objects.filter(pk=attempt.job_id).update(
        status=JobStatus.FAILED,
        stage="failed",
        safe_error=safe_error,
    )
    if attempt.job.media_id is not None:
        Media.objects.filter(pk=attempt.job.media_id).update(
            processing_status="failed",
            encoding_status=encoding_status_for("failed"),
        )


def _confirm_canceled(attempt, now):
    attempt.status = AttemptStatus.CANCELED
    attempt.completed_at = now
    MediaIngestionJob.objects.filter(pk=attempt.job_id).update(
        status=JobStatus.CANCELED,
        stage="canceled",
        safe_error="Processing was canceled.",
    )


def poll_attempt(attempt_id, gateway, now):
    attempt = MediaJobAttempt.objects.get(pk=attempt_id)
    if attempt.status in _TERMINAL_ATTEMPT_STATUSES:
        return PollDecision(None, True)
    if not attempt.mediaconvert_job_id:
        raise ValueError("Attempt has no MediaConvert Job ID.")

    try:
        snapshot = gateway.get_job(attempt.mediaconvert_job_id)
    except Exception as error:
        if _is_transient(error):
            return _retry_after_error(attempt_id, now)
        raise
    if snapshot.job_id != attempt.mediaconvert_job_id:
        raise InvalidMediaConvertEvidence("MediaConvert returned a different Job ID.")

    with transaction.atomic():
        attempt = (
            MediaJobAttempt.objects.select_for_update(of=("self",))
            .select_related("job__media")
            .get(pk=attempt_id)
        )
        if attempt.status in _TERMINAL_ATTEMPT_STATUSES:
            return PollDecision(None, True)
        evidence_changed = _changed(attempt, snapshot)
        attempt.provider_status = snapshot.status
        attempt.provider_phase = snapshot.phase or ""
        if snapshot.percent_complete is not None:
            provider_percent = Decimal(str(snapshot.percent_complete))
            attempt.provider_percent_complete = provider_percent
            if provider_percent > attempt.job.progress:
                MediaIngestionJob.objects.filter(pk=attempt.job_id).update(
                    progress=provider_percent
                )
        if evidence_changed:
            attempt.provider_last_changed_at = now
            attempt.provider_unchanged_count = 0
        else:
            attempt.provider_unchanged_count += 1

        root, state = _poll_state(attempt)
        state["error_count"] = 0
        state.pop("last_error_kind", None)
        _save_poll_state(attempt, root, state)
        attempt.diagnostic_error = ""

        if snapshot.status == "COMPLETE":
            _complete_provider(attempt, snapshot, now)
            attempt.next_poll_at = None
            decision = PollDecision(None, True)
        elif snapshot.status == "ERROR":
            _fail_provider(attempt, now)
            attempt.next_poll_at = None
            decision = PollDecision(None, True)
        elif snapshot.status == "CANCELED":
            _confirm_canceled(attempt, now)
            attempt.next_poll_at = None
            decision = PollDecision(None, True)
        else:
            _record_stall_or_timeout(attempt, now)
            delay = 10 if evidence_changed else _delay_for(
                attempt.provider_unchanged_count
            )
            attempt.next_poll_at = now + timedelta(seconds=delay)
            decision = PollDecision(delay, False)

        attempt.save(
            update_fields=(
                "status",
                "provider_status",
                "provider_phase",
                "provider_percent_complete",
                "provider_last_changed_at",
                "provider_unchanged_count",
                "checkpoint_evidence",
                "diagnostic_error",
                "next_poll_at",
                "completed_at",
                "updated_at",
            )
        )
    return decision
