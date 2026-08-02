# MediaConvert Core Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete a crash-recoverable, strictly serial AWS MediaConvert pipeline for one browser-uploaded video or audio file, from verified private-S3 original through HLS output verification, atomic publication and exact cleanup.

**Architecture:** Short Celery ticks execute one bounded orchestration action while PostgreSQL checkpoints and the singleton Processing Lease remain authoritative. Focused AWS adapters own S3 and MediaConvert calls; manifest verification produces an exact artifact inventory before existing asset-version activation publishes media atomically. Redis only wakes work and can be reconstructed by a periodic reconciler.

**Tech Stack:** Django 5.2, PostgreSQL 17, Celery 5.4, Redis, boto3/botocore, AWS MediaConvert Probe/Jobs, private S3, CloudFormation, pytest/pytest-django, FFprobe/FFmpeg only for acceptance-fixture trimming and validation on Arch.

## Global Constraints

- Scope is `source_type=upload` for one local video or one local audio file; HLS import publication, YouTube/subtitles, TaskView/Action API and history remain separate plans.
- PostgreSQL, S3 and MediaConvert are truth; Celery result state is never completion evidence.
- One Processing Lease globally enforces FIFO heavy processing even when multiple workers exist.
- Each tick performs at most one bounded external side effect and never sleeps while waiting for MediaConvert.
- Media bytes move browser → S3 and S3 → MediaConvert; Django never proxies complete media bytes.
- Browser upload promotion uses S3 server-side Copy to `originals/{media_id}/{attempt_id}/source.ext` before `source_verified` is completed.
- `ClientRequestToken=sha256(attempt_id + template_version + input_checksum)` is secondary protection; unknown CreateJob results are reconciled with `ListJobs` and are never blindly resubmitted.
- New Job tags are `Project`, `Environment`, `MediaId`, `JobId`, `AttemptId`, `SourceType`, `TemplateVersion`; `userMetadata` contains only `job_id/attempt_id`.
- No SNS, CloudWatch Alarm/Dashboard/custom metric, EventBridge, SQS or monitoring Lambda is added.
- Output verification starts from MediaConvert-returned paths, validates a complete local HLS dependency closure and never promotes unknown objects.
- Cleanup deletes only persisted exact managed keys/paths; cleanup failure never moves ready Media backward.
- AWS resource mutation remains CloudFormation-only. A reviewed Change Set is a blocking approval gate before its execution.
- Real dev acceptance uses the supplied sources only through temporary 20-second video and 30-second audio derivatives, strictly serially, and cleans all created test state.

## File Structure

- `files/models/ingestion.py`: Attempt coordination fields, exact `AttemptArtifact`, deduplicated `MediaJobWarning`.
- `files/models/uploads.py`: upload-to-original promotion intent and evidence.
- `files/services/processing_storage.py`: exact S3 Copy/Head/Get/List/Delete primitives for managed attempt roots.
- `files/services/media_probe.py`: normalized parsing of MediaConvert Probe evidence and rendition selection.
- `files/services/mediaconvert.py`: request construction, submit/list/get/cancel and provider normalization.
- `files/services/output_verification.py`: MediaConvert path validation and HLS dependency closure.
- `files/services/asset_publishing.py`: artifact-to-candidate registration and cancel-safe atomic activation.
- `files/services/processing_cleanup.py`: idempotent exact artifact cleanup.
- `files/services/processing_runner.py`: checkpoint state machine and one-action tick.
- `files/processing_tasks.py`: Celery tick/reconciler wrappers only.
- `files/management/commands/verify_mediaconvert_orchestration.py`: disposable dev video/audio acceptance.
- `infra/aws/mediacms-core.yaml`: least-privilege `ListJobs` addition.

## Locked Interfaces

The following names and signatures are fixed across tasks; implementations may add private helpers but must not rename these contracts:

```python
@dataclass(frozen=True, slots=True)
class ObjectEvidence:
    key: str
    size: int
    content_type: str
    checksum_sha256: str

@dataclass(frozen=True, slots=True)
class SourceFacts:
    media_type: str
    duration_seconds: float
    width: int | None
    height: int | None
    has_audio: bool

@dataclass(frozen=True, slots=True)
class ProviderSnapshot:
    job_id: str
    status: str
    phase: str | None
    percent_complete: float | None
    warnings: tuple[dict, ...]
    output_group_details: tuple[dict, ...]

@dataclass(frozen=True, slots=True)
class PollDecision:
    next_delay: int | None
    terminal: bool

@dataclass(frozen=True, slots=True)
class VerifiedOutput:
    key: str
    kind: str
    size: int
    content_type: str
    checksum: str

@dataclass(frozen=True, slots=True)
class VerifiedOutputSet:
    manifest_key: str
    outputs: tuple[VerifiedOutput, ...]

def promote_file_original(session_id: UUID, gateway) -> ObjectEvidence: ...
def probe_source(source_s3_uri: str, gateway) -> SourceFacts: ...
def allowed_video_heights(source_height: int) -> tuple[int, ...]: ...
def submission_token(attempt_id: UUID, template_version: str, input_checksum: str) -> str: ...
def prepare_submission(attempt_id: UUID, source_facts: SourceFacts): ...
def submit_prepared(attempt_id: UUID, gateway): ...
def reconcile_unknown_submission(attempt_id: UUID, gateway, now): ...
def poll_attempt(attempt_id: UUID, gateway, now) -> PollDecision: ...
def verify_mediaconvert_outputs(attempt_id: UUID, snapshot, storage) -> VerifiedOutputSet: ...
def register_candidate(attempt_id: UUID, outputs: VerifiedOutputSet): ...
def publish_candidate(attempt_id: UUID): ...
def request_attempt_cancel(job_id: UUID): ...
def reconcile_cancellation(attempt_id: UUID, gateway): ...
def cleanup_attempt(attempt_id: UUID, storage): ...
def run_processing_tick(owner_token: str, now=None) -> "TickResult": ...
```

---

### Task 1: Orchestration Persistence and Exact Artifact Ledger

**Files:**
- Modify: `files/models/ingestion.py`
- Modify: `files/models/uploads.py`
- Modify: `files/models/__init__.py`
- Create: `files/migrations/0025_mediaconvert_orchestration.py`
- Create: `tests/aws_orchestration/test_orchestration_models.py`

**Interfaces:**
- Produces `AttemptArtifact`, `MediaJobWarning`, `ArtifactPurpose`, `ArtifactCleanupStatus`.
- Adds Attempt fields named exactly as approved and BrowserUploadObject fields `promoted_s3_key`, `promotion_status`.

- [ ] **Step 1: Write failing persistence tests.** Assert Attempt defaults; unique artifact `(attempt,s3_key)`; allowed managed roots; warning uniqueness `(attempt,code)`; promotion states `pending|copying|verified|failed`; index due ticks on `(status,next_poll_at)`.
- [ ] **Step 2: Verify RED.** Run PostgreSQL pytest for `test_orchestration_models.py`; expect import/field failures.
- [ ] **Step 3: Implement models.** Add Attempt strings/timestamps/count, `AttemptArtifact(attempt,purpose,s3_key,size_bytes,content_type,checksum,cleanup_status,safe_error,timestamps)`, and `MediaJobWarning(attempt,code,message,acknowledged_at,created_at)` with no secret-bearing `__str__`.
- [ ] **Step 4: Generate migration.** Run `manage.py makemigrations files`; inspect names and dependencies; run `makemigrations --check --dry-run`.
- [ ] **Step 5: Verify GREEN and commit.** Run focused tests and `git commit -m "feat: add orchestration artifact ledger"`.

### Task 2: Managed Processing Storage Adapter

**Files:**
- Create: `files/services/processing_storage.py`
- Create: `tests/aws_orchestration/test_processing_storage.py`
- Modify: `cms/settings.py`

**Interfaces:**
- Produces `ProcessingStorageGateway.copy_exact`, `head_exact`, `presign_get`, `get_text`, `list_attempt_candidates`, `delete_exact` and immutable `ObjectEvidence`.
- Accepts only server-generated `uploads/`, `originals/`, `candidates/` keys and the configured Bucket.

- [ ] **Step 1: Write recording-client tests.** Cover `CopySource`, metadata directive, SigV4 GET, pagination, UTF-8/size bounds, checksum evidence, 404 idempotent delete and rejection of traversal, foreign Bucket or unrelated prefix.
- [ ] **Step 2: Verify RED** on the missing module.
- [ ] **Step 3: Implement adapter** with lazy boto3 SigV4 client, exact key validators and `AWS_MANIFEST_MAX_BYTES=1048576`.
- [ ] **Step 4: Verify GREEN** with no real AWS call.
- [ ] **Step 5: Commit:** `git commit -m "feat: add managed processing storage adapter"`.

### Task 3: Upload Completion Promotes Originals Before Queueing

**Files:**
- Modify: `files/services/upload_sessions.py`
- Modify: `files/services/s3_uploads.py`
- Modify: `tests/aws_ingestion/test_upload_sessions.py`
- Create: `tests/aws_orchestration/test_original_promotion.py`

**Interfaces:**
- Produces `promote_file_original(session_id, storage) -> ObjectEvidence` as an idempotent saga.
- Changes `complete_file_upload` so `source_verified` evidence names the verified `originals/` key, upload object ID, size, type and checksum.
- Changes the application boundary to `complete_file_upload(..., gateway, promotion_storage)`; the API constructs both focused adapters.

- [ ] **Step 1: Write failing promotion tests.** Cover exact destination using pre-created Attempt ID, server-side Copy, Head mismatch, copy-success/DB-crash recovery, repeated completion, and no enqueue/checkpoint before original verification.
- [ ] **Step 2: Verify RED.** Existing completion tests must expose their old `uploads/` evidence assumption.
- [ ] **Step 3: Use `ProcessingStorageGateway`** for exact same-Bucket Copy while preserving source checksum/type metadata; do not duplicate Copy rules in the upload adapter.
- [ ] **Step 4: Refactor completion saga.** Create/get Attempt sequence 1 before promotion; persist `copying` intent and artifact rows transactionally; Copy and Head outside transaction; finalize `source_verified`, session completion and enqueue transactionally.
- [ ] **Step 5: Prove recovery.** Simulate Copy success followed by interrupted Head and verify retry Heads the deterministic original without recopying or duplicating Attempt.
- [ ] **Step 6: Run all ingestion/domain tests and commit:** `git commit -m "fix: verify original before processing enqueue"`.

### Task 4: Least-Privilege Probe and ListJobs Infrastructure Change

**Files:**
- Modify: `infra/aws/mediacms-core.yaml`
- Modify: `tests/aws_infrastructure/test_core_template_contract.py`
- Modify: `infra/aws/README.md`

**Interfaces:**
- Produces Runtime permissions `mediaconvert:Probe` and `mediaconvert:ListJobs` in a dedicated statement with `Resource: '*'`.
- Preserves ARN-scoped `GetJob/CancelJob` and tag-conditioned `CreateJob`.

- [ ] **Step 1: Write failing template tests.** Require exactly one wildcard Probe/ListJobs statement and prove no wildcard Get/Cancel, S3 object or IAM permission is introduced.
- [ ] **Step 2: Verify RED** with focused infrastructure pytest.
- [ ] **Step 3: Modify CloudFormation and documentation.** Do not add resources, alarms or outputs.
- [ ] **Step 4: Validate locally.** Run infrastructure pytest, `cfn-lint`, template-size check and `aws cloudformation validate-template --profile default --region us-east-1`.
- [ ] **Step 5: Commit:** `git commit -m "infra: allow mediaconvert job reconciliation"`.
- [ ] **Step 6: Create but do not execute a Change Set.** Use a unique reviewed name and describe it. Request explicit administrator approval, but continue offline/mock implementation Tasks 5–12 while approval is pending. Expected change: only Runtime managed inline policy update.
- [ ] **Step 7: After exact approval and before Task 13 real acceptance, execute and verify.** Prove Runtime `Probe` on an exact project original and `ListJobs` succeed while Probe of unrelated input, CloudFormation, CloudWatch writes, unrelated S3 and foreign-role PassRole remain denied.

### Task 5: MediaConvert Source Probe and Rendition Selection

**Files:**
- Create: `files/services/media_probe.py`
- Create: `tests/aws_orchestration/test_media_probe.py`
- Modify: `cms/settings.py`

**Interfaces:**
- Produces `SourceFacts(media_type,duration_seconds,width,height,has_audio)` and `probe_source(source_s3_uri,gateway)`.
- Produces `allowed_video_heights(source_height) -> tuple[int,...]`, never upscaling.
- Consumes a structural `ProbeGateway` with `probe(source_s3_uri) -> dict`; Task 6's `MediaConvertGateway` implements it.

- [ ] **Step 1: Write failing Probe request/parser tests.** Cover video/audio responses, odd dimensions, missing tracks/container, unsupported input, throttling and diagnostics that never expose local paths or signed URLs.
- [ ] **Step 2: Verify RED.** No actual AWS request in unit tests.
- [ ] **Step 3: Implement MediaConvert Probe.** Require an `s3://` URI in the configured Bucket beneath `originals/`; call `gateway.probe`; normalize duration, video dimensions and audio presence into immutable evidence.
- [ ] **Step 4: Implement ladder filter.** For video, return only `360,480,720,1080` heights not above normalized source height; dimensions used in overrides remain even. Audio returns no video ladder.
- [ ] **Step 5: Verify and commit:** `git commit -m "feat: probe aws source media safely"`.

### Task 6: MediaConvert Gateway and Deterministic Job Request

**Files:**
- Create: `files/services/mediaconvert.py`
- Create: `tests/aws_orchestration/test_mediaconvert_gateway.py`
- Modify: `cms/settings.py`

**Interfaces:**
- Produces `MediaConvertGateway.probe`, `create_job`, `list_jobs`, `get_job`, `cancel_job`.
- Produces `build_job_request(attempt, source, facts)`, `submission_token`, `match_reconciliation_job`, `ProviderSnapshot`.

- [ ] **Step 1: Write fake-client request tests.** Assert video/audio template selection, source/destination overrides, filtered ladder, Role ARN, disabled acceleration, exact tags, safe metadata and no title/path/URL/cookie values.
- [ ] **Step 2: Write provider normalization tests.** Cover `SUBMITTED|PROGRESSING|COMPLETE|CANCELED|ERROR`, `currentPhase`, nullable percent, warnings and output group details.
- [ ] **Step 3: Write reconciliation tests.** Paginate newest Jobs, require exact metadata/template/input/destination match, reject zero/multiple ambiguous matches, and never call Create during unknown-result reconciliation.
- [ ] **Step 4: Verify RED.** Expect missing gateway.
- [ ] **Step 5: Implement lazy boto3 adapter and settings:** `AWS_MEDIACONVERT_ROLE_ARN`, `AWS_MEDIACONVERT_VIDEO_TEMPLATE`, `AWS_MEDIACONVERT_AUDIO_TEMPLATE`, `AWS_ENVIRONMENT`, logical template version `h264-hls-qvbr-v1`.
- [ ] **Step 6: Verify and commit:** `git commit -m "feat: add idempotent mediaconvert gateway"`.

### Task 7: Submission Intent and Unknown-Result Recovery

**Files:**
- Create: `files/services/processing_submission.py`
- Create: `tests/aws_orchestration/test_processing_submission.py`

**Interfaces:**
- Produces `prepare_submission(attempt_id, source_facts)`, `submit_prepared(attempt_id,gateway)`, `reconcile_unknown_submission(attempt_id,gateway,now)`.
- Checkpoints `mediaconvert_submitting` and `mediaconvert_submitted` use immutable evidence.

- [ ] **Step 1: Write failing transactional intent tests.** Intent is durable before Create; existing Job ID prevents Create; token/input/template changes conflict.
- [ ] **Step 2: Write crash-window tests.** Fake Create records success then raises timeout; retry uses one-minute Token window and ListJobs; exact match stores one Job ID.
- [ ] **Step 3: Write no-proof tests.** After bounded reconciliation with no/ambiguous match, set safe action-required failure and do not issue another Create.
- [ ] **Step 4: Implement minimal submission service** with transactions around intent/finalization but no database lock held during AWS calls.
- [ ] **Step 5: Verify focused and PostgreSQL concurrency tests.** Two callers must produce at most one Create intent owner.
- [ ] **Step 6: Commit:** `git commit -m "feat: recover mediaconvert submission intent"`.

### Task 8: Adaptive Provider Polling and Warning Persistence

**Files:**
- Create: `files/services/processing_polling.py`
- Create: `tests/aws_orchestration/test_processing_polling.py`

**Interfaces:**
- Produces `poll_attempt(attempt_id,gateway,now) -> PollDecision(next_delay,terminal)`.
- Produces warning codes `submitted_stalled`, `progress_stalled`, `processing_timeout`.

- [ ] **Step 1: Write time-controlled tests.** Change →10s, unchanged count 2→30s, count 5→60s; real percent only; monotonic Job progress.
- [ ] **Step 2: Cover warnings and errors.** Deduplicate 30-minute warnings; total 6-hour timeout requests cancel; throttling/network/5xx preserve provider evidence and return bounded jittered retry.
- [ ] **Step 3: Cover terminal states.** COMPLETE records checkpoint only; ERROR sanitizes user error and retains restricted diagnostic; CANCELED waits for AWS confirmation.
- [ ] **Step 4: Implement without sleeps** and persist `next_poll_at`.
- [ ] **Step 5: Verify and commit:** `git commit -m "feat: reconcile mediaconvert job progress"`.

### Task 9: Output Path and HLS Closure Verification

**Files:**
- Create: `files/services/output_verification.py`
- Create: `tests/aws_orchestration/test_output_verification.py`

**Interfaces:**
- Produces `verify_mediaconvert_outputs(attempt_id,snapshot,storage) -> VerifiedOutputSet`.
- Produces exact typed artifacts for master, variants, segments/init maps and video image.

- [ ] **Step 1: Write pure path/manifest tests.** Reject foreign Bucket, wrong Attempt prefix, traversal, external/query/fragment URI, encryption, duplicate master, oversized/non-UTF8 manifest.
- [ ] **Step 2: Write video/audio contract tests.** Video requires master+variant+image; audio requires master+audio variant and no image; every dependency must Head nonzero with allowed type.
- [ ] **Step 3: Write artifact inventory tests.** Candidate prefix listing is bounded/paginated; all keys become `AttemptArtifact`; only closure keys become publishable outputs; missing and unexpected business references fail.
- [ ] **Step 4: Verify RED and implement** using POSIX relative resolution shared with HLS safety helpers where semantics match.
- [ ] **Step 5: Verify and commit:** `git commit -m "feat: verify mediaconvert output closure"`.

### Task 10: Candidate Publication and Cancel-Safe Activation

**Files:**
- Create: `files/services/asset_publishing.py`
- Modify: `files/services/media_state.py`
- Create: `tests/aws_orchestration/test_asset_publishing.py`

**Interfaces:**
- Produces `register_candidate(attempt_id, outputs)` and `publish_candidate(attempt_id)`.
- Completes `outputs_verified`, `assets_activated`, `media_published` in order.

- [ ] **Step 1: Write candidate tests.** Idempotent artifact-to-asset mapping, exact manifest key, checksum/size/type persistence and no activation before complete closure.
- [ ] **Step 2: Write transaction/race tests.** Lock Media/Job/Attempt/version; cancellation before commit prevents activation; injected transaction failure leaves old active and Media state unchanged.
- [ ] **Step 3: Implement focused publishing service** reusing `activate_asset_version` after cancel-safe validation.
- [ ] **Step 4: Verify ready compatibility fields** and replacement preservation.
- [ ] **Step 5: Commit:** `git commit -m "feat: atomically publish mediaconvert assets"`.

### Task 11: Cooperative Cancellation and Exact Cleanup

**Files:**
- Create: `files/services/processing_cleanup.py`
- Create: `files/services/processing_cancellation.py`
- Create: `tests/aws_orchestration/test_processing_cleanup.py`
- Create: `tests/aws_orchestration/test_processing_cancellation.py`

**Interfaces:**
- Produces `request_attempt_cancel`, `reconcile_cancellation`, `cleanup_attempt`.
- Cleanup consumes only `AttemptArtifact` rows and active-version membership.

- [ ] **Step 1: Write cancellation phase tests.** Before submission, SUBMITTED/PROGRESSING, COMPLETE race, ERROR and confirmed CANCELED; CancelJob called once; no activation after request.
- [ ] **Step 2: Write cleanup tests.** Success deletes upload/original and retains active candidate; failure/cancel deletes original/candidate and retains previous active; 404 is success; per-object errors persist.
- [ ] **Step 3: Prove independent cleanup state.** A ready Media remains ready when one deletion fails; retry only visits pending/failed artifacts.
- [ ] **Step 4: Implement cancellation and cleanup** with managed-root validation and no prefix deletion.
- [ ] **Step 5: Verify and commit:** `git commit -m "feat: cancel and clean processing attempts"`.

### Task 12: One-Action Runner, Celery Tick and PostgreSQL Reconciler

**Files:**
- Create: `files/services/processing_runner.py`
- Create: `files/processing_tasks.py`
- Modify: `cms/settings.py`
- Create: `tests/aws_orchestration/test_processing_runner.py`
- Create: `tests/aws_orchestration/test_processing_tasks.py`

**Interfaces:**
- Produces `run_processing_tick(owner_token,now=None) -> TickResult`.
- Celery tasks `aws_processing_tick(job_id=None)` and `reconcile_aws_processing()`.

- [ ] **Step 1: Write state-machine tests.** One tick performs exactly one of probe/intent/submit/poll/verify/publish/cleanup; it checks lease ownership and cancel flag first.
- [ ] **Step 2: Write scheduling tests.** Tick uses `apply_async(countdown=...)`; no sleep; duplicate wakeups are harmless; only the lease-bound Job is polled.
- [ ] **Step 3: Write Redis-loss tests.** With no scheduled task, reconciler discovers queued/due running PostgreSQL work and emits one safe wakeup; it never creates duplicate Attempt or submission.
- [ ] **Step 4: Implement runner and thin Celery wrappers.** Add Beat every minute only for reconciliation; adaptive ticks self-schedule.
- [ ] **Step 5: Verify eager-mode, PostgreSQL race and all AWS domain suites.** Run the focused task tests plus `tests/aws_domain`, `tests/aws_ingestion` and `tests/aws_orchestration` against PostgreSQL 17.
- [ ] **Step 6: Commit:** `git commit -m "feat: orchestrate aws processing ticks"`.

### Task 13: Disposable Real Video and Audio Acceptance

**Files:**
- Create: `files/management/commands/verify_mediaconvert_orchestration.py`
- Create: `tests/aws_orchestration/test_acceptance_command.py`
- Modify: `infra/aws/README.md`
- Modify: `docs/superpowers/plans/2026-08-02-aws-integration-roadmap.md`

**Interfaces:**
- Command arguments `--video-source`, `--audio-source`, `--stack mediacms-dev`, `--region us-east-1`.
- Produces a non-secret PASS summary and always executes exact cleanup.

- [ ] **Step 1: Write mocked command tests.** Validate source existence, refuse symlinks/non-files, build FFmpeg argument lists without shell, video 20s/audio 30s, and cleanup in `finally` on every failure.
- [ ] **Step 2: Implement fixture preparation.** Use a private `mkdtemp` directory; try stream copy, validate with FFprobe, then compatibility-encode only the disposable derivative if required.
- [ ] **Step 3: Implement strict serial acceptance.** Create isolated DB/upload-session state, place each derivative beneath its generated `uploads/` key, invoke the real completion promotion, then run synchronous ticks through ready+cleanup for video followed by audio; record Job IDs/template versions/status only.
- [ ] **Step 4: Implement cleanup guard.** Delete only command-created S3 keys and DB rows after checking IDs/prefixes; local temp directory always removed; source files never opened for write.
- [ ] **Step 5: Run all local gates first.** PostgreSQL suites, infrastructure tests, flake8, migration checks, CloudFormation validation and secret scan.
- [ ] **Step 6: Run real dev acceptance** with the administrator-approved source paths as CLI arguments. Confirm no verification S3 object/Multipart or test DB record remains.
- [ ] **Step 7: Record non-secret evidence, mark roadmap phase 4 core complete and commit:** `git commit -m "docs: verify mediaconvert core orchestration"`.

## Completion Gate

- Upload completion does not enqueue until the deterministic `originals/` object is verified.
- FIFO Processing Lease and short ticks prevent concurrent heavy work and long-lived sleeping workers.
- Unknown CreateJob outcomes reconcile by exact metadata/template/input/destination and never blindly resubmit.
- Runtime can ListJobs but retains all prior negative IAM boundaries.
- Provider polling persists only real status/phase/percent and adapts 10/30/60 seconds without fake progress.
- COMPLETE with any missing/unsafe HLS dependency or required image does not activate.
- Candidate activation is atomic and cancel-safe; replacement failure preserves old playback.
- Cleanup is exact, idempotent and independent from ready Media state.
- Redis scheduling loss is recoverable from PostgreSQL without duplicate Attempt or MediaConvert Job.
- Real 20-second video and 30-second audio dev jobs pass serially and leave no test artifacts.
