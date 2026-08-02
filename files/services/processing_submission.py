import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from hashlib import sha256

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from files.models import (
    ArtifactPurpose,
    AttemptArtifact,
    Media,
    MediaIngestionJob,
    MediaJobAttempt,
    MediaJobCheckpoint,
)
from files.models.domain import encoding_status_for
from files.models.ingestion import AttemptStatus, CheckpointStatus, JobStatus
from files.services.media_probe import SourceFacts
from files.services.mediaconvert import (
    AmbiguousReconciliation,
    build_job_request,
    match_reconciliation_job,
)
from files.services.processing_storage import ObjectEvidence


class SubmissionIntentConflict(RuntimeError):
    pass


class SubmissionOutcomeUnknown(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedSubmission:
    request: dict
    request_fingerprint: str
    owner: bool


def _fingerprint(request):
    encoded = json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _original_source(attempt):
    artifacts = list(
        AttemptArtifact.objects.filter(
            attempt=attempt,
            purpose=ArtifactPurpose.ORIGINAL,
        )[:2]
    )
    if len(artifacts) != 1:
        raise SubmissionIntentConflict("Submission intent requires one verified original.")
    artifact = artifacts[0]
    return ObjectEvidence(
        key=artifact.s3_key,
        size=artifact.size_bytes,
        content_type=artifact.content_type,
        checksum_sha256=artifact.checksum,
    )


def _facts_dict(source_facts):
    return asdict(source_facts)


def _facts_from_evidence(evidence):
    try:
        return SourceFacts(**evidence["source_facts"])
    except (KeyError, TypeError) as error:
        raise SubmissionIntentConflict("Submission intent source evidence is invalid.") from error


def _immutable_evidence(attempt, source, source_facts, request, fingerprint):
    return {
        "template_name": request["JobTemplate"],
        "template_version": settings.AWS_MEDIACONVERT_TEMPLATE_VERSION,
        "client_request_token": request["ClientRequestToken"],
        "input_key": source.key,
        "input_checksum": source.checksum_sha256,
        "candidate_prefix": f"candidates/{attempt.job.media_id}/{attempt.id}/",
        "source_facts": _facts_dict(source_facts),
        "request_fingerprint": fingerprint,
    }


def _request_from_intent(attempt, checkpoint):
    source = _original_source(attempt)
    source_facts = _facts_from_evidence(checkpoint.evidence)
    request = build_job_request(attempt, source, source_facts)
    fingerprint = _fingerprint(request)
    expected = _immutable_evidence(
        attempt,
        source,
        source_facts,
        request,
        fingerprint,
    )
    if checkpoint.evidence != expected:
        raise SubmissionIntentConflict("Current source or template conflicts with submission intent.")
    if any(
        (
            attempt.template_name != expected["template_name"],
            attempt.template_version != expected["template_version"],
            attempt.client_request_token != expected["client_request_token"],
        )
    ):
        raise SubmissionIntentConflict("Attempt fields conflict with submission intent.")
    return request, fingerprint


def prepare_submission(attempt_id, source_facts):
    with transaction.atomic():
        attempt = (
            MediaJobAttempt.objects.select_for_update(of=("self",))
            .select_related("job__media")
            .get(pk=attempt_id)
        )
        source = _original_source(attempt)
        request = build_job_request(attempt, source, source_facts)
        fingerprint = _fingerprint(request)
        evidence = _immutable_evidence(
            attempt,
            source,
            source_facts,
            request,
            fingerprint,
        )
        checkpoint = MediaJobCheckpoint.objects.filter(
            attempt=attempt,
            name="mediaconvert_submitting",
        ).first()
        if checkpoint is not None:
            if checkpoint.evidence != evidence:
                raise SubmissionIntentConflict(
                    "Current source or template conflicts with submission intent."
                )
            _request_from_intent(attempt, checkpoint)
            return PreparedSubmission(request, fingerprint, False)

        if any(
            (
                attempt.template_name,
                attempt.template_version,
                attempt.client_request_token,
                attempt.submission_intent_at,
            )
        ):
            raise SubmissionIntentConflict("Attempt has an incomplete submission intent.")
        now = timezone.now()
        attempt.template_name = evidence["template_name"]
        attempt.template_version = evidence["template_version"]
        attempt.client_request_token = evidence["client_request_token"]
        attempt.submission_intent_at = now
        attempt.save(
            update_fields=(
                "template_name",
                "template_version",
                "client_request_token",
                "submission_intent_at",
                "updated_at",
            )
        )
        MediaJobCheckpoint.objects.create(
            attempt=attempt,
            name="mediaconvert_submitting",
            status=CheckpointStatus.COMPLETED,
            evidence=evidence,
            completed_at=now,
        )
        MediaIngestionJob.objects.filter(pk=attempt.job_id).update(
            stage="mediaconvert_submitting"
        )
        return PreparedSubmission(request, fingerprint, True)


def _coordination(attempt):
    root = dict(attempt.checkpoint_evidence)
    coordination = dict(root.get("mediaconvert_submission", {}))
    return root, coordination


def _save_coordination(attempt, root, coordination, provider_status=None):
    root["mediaconvert_submission"] = coordination
    attempt.checkpoint_evidence = root
    fields = ["checkpoint_evidence", "updated_at"]
    if provider_status is not None:
        attempt.provider_status = provider_status
        fields.append("provider_status")
    attempt.save(update_fields=tuple(fields))


def _load_locked_prepared(attempt_id):
    attempt = (
        MediaJobAttempt.objects.select_for_update(of=("self",))
        .select_related("job__media")
        .get(pk=attempt_id)
    )
    try:
        checkpoint = MediaJobCheckpoint.objects.get(
            attempt=attempt,
            name="mediaconvert_submitting",
        )
    except MediaJobCheckpoint.DoesNotExist as error:
        raise SubmissionIntentConflict("Submission intent is not prepared.") from error
    request, _ = _request_from_intent(attempt, checkpoint)
    return attempt, request


def _finalize_submission(attempt_id, job_id, now=None):
    completed_at = now or timezone.now()
    with transaction.atomic():
        attempt, _ = _load_locked_prepared(attempt_id)
        if attempt.mediaconvert_job_id:
            if attempt.mediaconvert_job_id != job_id:
                raise SubmissionIntentConflict("A different MediaConvert Job is already stored.")
            return attempt.mediaconvert_job_id
        attempt.mediaconvert_job_id = job_id
        attempt.provider_status = "SUBMITTED"
        attempt.save(
            update_fields=(
                "mediaconvert_job_id",
                "provider_status",
                "updated_at",
            )
        )
        MediaJobCheckpoint.objects.update_or_create(
            attempt=attempt,
            name="mediaconvert_submitted",
            defaults={
                "status": CheckpointStatus.COMPLETED,
                "evidence": {
                    "job_id": job_id,
                    "template_name": attempt.template_name,
                    "template_version": attempt.template_version,
                    "client_request_token": attempt.client_request_token,
                },
                "completed_at": completed_at,
            },
        )
        MediaIngestionJob.objects.filter(pk=attempt.job_id).update(
            stage="mediaconvert_submitted"
        )
    return job_id


def submit_prepared(attempt_id, gateway):
    with transaction.atomic():
        attempt, request = _load_locked_prepared(attempt_id)
        if attempt.mediaconvert_job_id:
            return attempt.mediaconvert_job_id
        root, coordination = _coordination(attempt)
        if coordination.get("create_attempts", 0) >= 1:
            raise SubmissionOutcomeUnknown(
                "MediaConvert submission outcome requires reconciliation."
            )
        coordination["create_attempts"] = 1
        coordination["create_started_at"] = timezone.now().isoformat()
        coordination["token_retry_used"] = False
        coordination["reconciliation_count"] = 0
        _save_coordination(attempt, root, coordination, "CREATE_IN_FLIGHT")

    try:
        job_id = gateway.create_job(request)
    except (TimeoutError, ConnectionError) as error:
        raise SubmissionOutcomeUnknown(
            "MediaConvert submission outcome requires reconciliation."
        ) from error
    return _finalize_submission(attempt_id, job_id)


def _mark_action_required(attempt):
    safe_error = "Processing needs administrator review before retry."
    attempt.status = AttemptStatus.FAILED
    attempt.provider_status = "SUBMISSION_UNKNOWN"
    attempt.diagnostic_error = (
        "MediaConvert submission outcome could not be proven after bounded reconciliation."
    )
    attempt.save(
        update_fields=(
            "status",
            "provider_status",
            "diagnostic_error",
            "updated_at",
        )
    )
    MediaIngestionJob.objects.filter(pk=attempt.job_id).update(
        status=JobStatus.FAILED,
        stage="action_required",
        safe_error=safe_error,
    )
    if attempt.job.media_id is not None:
        Media.objects.filter(pk=attempt.job.media_id).update(
            processing_status="failed",
            encoding_status=encoding_status_for("failed"),
        )


def reconcile_unknown_submission(attempt_id, gateway, now):
    retry_with_token = False
    with transaction.atomic():
        attempt, request = _load_locked_prepared(attempt_id)
        if attempt.mediaconvert_job_id:
            return attempt.mediaconvert_job_id
        root, coordination = _coordination(attempt)
        if not coordination.get("create_attempts"):
            raise SubmissionIntentConflict("MediaConvert CreateJob has not started.")
        token_window = timedelta(seconds=settings.AWS_MEDIACONVERT_TOKEN_WINDOW_SECONDS)
        if (
            now <= attempt.submission_intent_at + token_window
            and coordination.get("token_retry_used") is not True
        ):
            retry_with_token = True
            coordination["token_retry_used"] = True
            coordination["create_attempts"] = coordination.get("create_attempts", 0) + 1
            _save_coordination(attempt, root, coordination, "CREATE_IN_FLIGHT")

    if retry_with_token:
        try:
            job_id = gateway.create_job(request)
        except (TimeoutError, ConnectionError) as error:
            raise SubmissionOutcomeUnknown(
                "MediaConvert submission outcome requires reconciliation."
            ) from error
        return _finalize_submission(attempt_id, job_id, now)

    jobs = gateway.list_jobs()
    ambiguous = False
    try:
        match = match_reconciliation_job(jobs, request)
    except AmbiguousReconciliation:
        match = None
        ambiguous = True
    if match is not None:
        return _finalize_submission(attempt_id, match["Id"], now)

    with transaction.atomic():
        attempt, _ = _load_locked_prepared(attempt_id)
        if attempt.mediaconvert_job_id:
            return attempt.mediaconvert_job_id
        root, coordination = _coordination(attempt)
        coordination["reconciliation_count"] = (
            coordination.get("reconciliation_count", 0) + 1
        )
        coordination["last_result"] = "ambiguous" if ambiguous else "not_found"
        _save_coordination(attempt, root, coordination, "SUBMISSION_UNKNOWN")
        if coordination["reconciliation_count"] >= settings.AWS_MEDIACONVERT_RECONCILIATION_LIMIT:
            _mark_action_required(attempt)
        else:
            attempt.next_poll_at = now + timedelta(seconds=30)
            attempt.save(update_fields=("next_poll_at", "updated_at"))
    return None
