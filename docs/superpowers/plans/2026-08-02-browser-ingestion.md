# Browser Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide an administrator-only, resumable browser-to-private-S3 protocol for one local video/audio file or a browser-expanded HLS package, without sending media bytes through Django or Cloudflare Tunnel.

**Architecture:** Django owns upload metadata, an exclusive FIFO upload lease, presigned-request issuance, S3 reconciliation and transition to `source_verified`. Files use S3 Multipart Upload; HLS packages register a validated relative file tree and use single PUT for small entries or Multipart for large entries. Browser UI and IndexedDB are implemented later against this API.

**Tech Stack:** Django 5.2, Django REST Framework, PostgreSQL 17, boto3/botocore, pytest/pytest-django, private Amazon S3.

## Global Constraints

- New API names, fields and user-facing errors are English; access requires `IsSiteAdministrator`.
- Only one upload session may hold the database-backed upload lease across all browsers and devices.
- Media bytes never pass through Django or Cloudflare Tunnel; presigned URLs expire after 15 minutes and are never persisted.
- Default Part size is 16 MiB; no non-final Multipart Part may be below S3's 5 MiB minimum.
- Object keys are generated only by Django beneath `uploads/{job_id}/{session_id}/`.
- Completion and recovery trust S3 `ListParts` and `HeadObject`, never browser claims alone.
- HLS limits: 10,000 files, depth 16, 2 GiB per entry, 20 GiB expanded total and compression ratio 100.
- HLS rejects absolute/traversal/backslash/NUL/duplicate/symlink paths, unsupported extensions, external URIs, encryption and DRM.
- AWS resources remain CloudFormation-owned. Cleanup uses stored exact keys and Multipart IDs only.
- Large dependency downloads are performed manually by the administrator.

## File Structure

- `files/models/uploads.py`: session, object, Part and lease persistence.
- `files/services/upload_lease.py`: transactional FIFO upload lease.
- `files/services/s3_uploads.py`: S3 Multipart, presign, reconcile and exact cleanup adapter.
- `files/services/upload_sessions.py`: file/HLS upload application service.
- `files/services/hls_package.py`: pure HLS path, inventory and dependency validation.
- `files/views/aws_uploads.py`: administrator-only DRF boundary.
- `tests/aws_ingestion/`: model, service, security and API contracts.

---

### Task 1: Upload Persistence and Constraints

**Files:**
- Create: `files/models/uploads.py`
- Modify: `files/models/__init__.py`
- Create: `files/migrations/0024_browser_upload_models.py`
- Create: `tests/aws_ingestion/test_upload_models.py`

**Interfaces:** Produces `BrowserUploadSession`, `BrowserUploadObject`, `BrowserUploadPart`, `BrowserUploadLease`; session states `waiting|uploading|paused|verifying|completed|canceled|expired|failed`; strategies `multipart|single_put`.

- [ ] **Step 1: Write failing model tests** for server-generated prefix, UUID IDs, positive sizes, source kind `file|hls`, unique create idempotency key, unique normalized path, Part range `1..10000`, unique `(object, part_number)` and singleton lease.
- [ ] **Step 2: Verify RED:** `.venv/bin/pytest tests/aws_ingestion/test_upload_models.py -q` must fail because `files.models.uploads` is absent.
- [ ] **Step 3: Implement models.** Session references Job/User with `PROTECT`, stores totals, confirmed progress, fingerprint, Part size, expiry, revisions/idempotency keys and safe error. Object stores normalized relative path, server key, size/type, strategy, optional S3 upload ID/checksum and state. Part stores authoritative number/ETag/size. Lease uses constrained key `default`, session/job, owner token, heartbeat and expiry.
- [ ] **Step 4: Generate migration and verify:** `manage.py makemigrations files`, then `manage.py makemigrations --check --dry-run` and focused pytest.
- [ ] **Step 5: Commit:** `git commit -m "feat: add browser upload persistence"`.

### Task 2: Exclusive FIFO Upload Lease

**Files:**
- Create: `files/services/upload_lease.py`
- Create: `tests/aws_ingestion/test_upload_lease.py`

**Interfaces:** `acquire_upload_lease(session_id, owner_token, lease_seconds, now=None) -> UploadLeaseGrant`, `heartbeat_upload_lease`, `require_upload_lease`, `release_upload_lease`; exceptions `UploadQueueBlocked(position)`, `UploadLeaseConflict`, `UploadLeaseExpired`.

- [ ] **Step 1: Write failing tests** for FIFO head, queue position, same-owner idempotency, live foreign owner, heartbeat, wrong token, expiry takeover, release and two PostgreSQL connections racing with one winner.
- [ ] **Step 2: Verify RED:** focused pytest must fail on missing service.
- [ ] **Step 3: Implement under `transaction.atomic()`** using singleton and session `select_for_update()`, eligible order `created_at,id`, exact session/token checks and no client-clock trust.
- [ ] **Step 4: Verify GREEN:** `.venv/bin/pytest tests/aws_ingestion/test_upload_lease.py -q`.
- [ ] **Step 5: Commit:** `git commit -m "feat: enforce fifo browser upload lease"`.

### Task 3: Private S3 Upload Adapter

**Files:**
- Modify: `requirements.txt`
- Modify: `cms/settings.py`
- Create: `files/services/s3_uploads.py`
- Create: `tests/aws_ingestion/test_s3_uploads.py`

**Interfaces:** `S3UploadGateway(client=None)` with `create_multipart`, `presign_part`, `presign_put`, paginated `list_parts`, `complete_multipart`, `head_object`, `abort_multipart`, `delete_exact_keys`; immutable `S3Part`, `PresignedRequest`, `S3ObjectEvidence`.

- [ ] **Step 1: Write failing recording-fake tests** proving every call is bound to configured Bucket, generated key, upload ID, Part number and TTL; cover pagination, quoted ETags, checksum evidence, and rejection of keys outside `uploads/`.
- [ ] **Step 2: Verify RED** on missing adapter.
- [ ] **Step 3: Add `boto3==1.40.38` and settings** `AWS_MEDIA_BUCKET`, `AWS_REGION=us-east-1`, `AWS_UPLOAD_PRESIGN_TTL_SECONDS=900`, `AWS_UPLOAD_PART_SIZE=16777216`; implement a lazy boto3 factory so fake-client tests do not import boto3.
- [ ] **Step 4: Stop at dependency gate.** Administrator runs `uv pip install --python .venv/bin/python -r requirements-dev.txt`, then verifies `import boto3`. Expected boto3/botocore/s3transfer/jmespath footprint is under roughly 30 MiB.
- [ ] **Step 5: Verify GREEN and commit:** focused pytest, then `git commit -m "feat: add private s3 upload adapter"`.

### Task 4: Resumable Video/Audio Upload Service

**Files:**
- Create: `files/services/upload_sessions.py`
- Create: `tests/aws_ingestion/test_upload_sessions.py`

**Interfaces:** `CreateFileSession`; `create_file_session`, `issue_part_urls`, `reconcile_parts`, `complete_file_upload`, `get_resume_snapshot`, `pause_upload`, `cancel_upload`.

- [ ] **Step 1: Write failing create/idempotency tests.** The same key/payload returns one draft AWS Media, Job, Session and Object; conflicting reuse is `409`; extensions/types are allowlisted.
- [ ] **Step 2: Verify RED, implement transactional creation** and confirm processing is not queued yet.
- [ ] **Step 3: Write failing lease/presign/reconcile tests.** Require lease, bound Part range, maximum 20 URLs/request, S3 `ListParts` replacing browser claims and authoritative progress.
- [ ] **Step 4: Write failing completion tests.** Require contiguous Parts, minimum non-final sizes, exact `HeadObject` size/type, completion idempotency and a `source_verified` checkpoint; Media must not become ready.
- [ ] **Step 5: Implement completion** with CompleteMultipart once, HeadObject verification, evidence persistence, lease release and FIFO processing enqueue.
- [ ] **Step 6: Write cancellation tests and implement exact abort/delete** for stored IDs/keys only; repeated cancellation returns the same snapshot.
- [ ] **Step 7: Verify suite and commit:** `git commit -m "feat: implement resumable file upload sessions"`.

### Task 5: Browser-Expanded HLS Package

**Files:**
- Create: `files/services/hls_package.py`
- Modify: `files/services/upload_sessions.py`
- Create: `tests/aws_ingestion/test_hls_package.py`
- Create: `tests/aws_ingestion/test_hls_upload_sessions.py`

**Interfaces:** `normalize_hls_path`, `validate_hls_inventory`, `validate_hls_manifests`, `register_hls_inventory`, `issue_hls_object_url`, `complete_hls_upload`; allowed suffixes `.m3u8,.ts,.m4s,.mp4,.aac,.vtt,.srt,.jpg,.jpeg,.png,.webp`.

- [ ] **Step 1: Write failing pure safety tests** for every path/ZIP limit, normalized duplicates, symlink, suffix, ambiguous entry, missing local dependency, external URI, `EXT-X-KEY`, session key and DRM.
- [ ] **Step 2: Verify RED; implement bounded UTF-8 manifest parsing** using POSIX relative resolution and a complete local dependency closure.
- [ ] **Step 3: Write failing session tests** for generated keys, size-based strategy, idempotent batches of at most 200 entries, lease enforcement and exact Head size/type evidence.
- [ ] **Step 4: Implement registration and completion.** Small entries use `PutObject`, large entries Multipart; completion requires all inventory objects verified and stores entry path plus verified object IDs, never URLs.
- [ ] **Step 5: Verify and commit:** `git commit -m "feat: validate browser hls package imports"`.

### Task 6: Administrator Upload API

**Files:**
- Create: `files/views/aws_uploads.py`
- Modify: `files/views/__init__.py`
- Modify: `files/urls.py`
- Create: `tests/aws_ingestion/test_upload_api.py`

**Interfaces:** `/api/v1/aws/uploads/` create/detail, lease acquire/heartbeat/release, object registration, URL issuance, reconcile, complete and cancel. Mutations require `Idempotency-Key`; lease-bound mutations require `Upload-Lease-Token`; completion requires `If-Match` revision.

- [ ] **Step 1: Write failing permission/schema tests** for anonymous/ordinary rejection, singleton-admin acceptance, JSON-only bodies, unknown-field rejection, stable English error codes and absence of persisted URLs in detail responses.
- [ ] **Step 2: Verify RED and implement thin DRF views** with explicit serializers. Map conflict `409`, stale revision `412`, invalid input `400`, expired/foreign lease `423`, missing session `404`.
- [ ] **Step 3: Write failing fake-S3 API flows** for create → lease → URL → reconcile → complete; refresh/resume, URL renewal, retry, pause, queue position, cancel and HLS inventory batches.
- [ ] **Step 4: Implement response projection** exposing IDs, revision, status/stage, queue position, confirmed/total bytes/files, Part size, expiry and allowed actions; URLs appear only in issuance responses.
- [ ] **Step 5: Verify and commit:** `git commit -m "feat: expose administrator browser upload api"`.

### Task 7: PostgreSQL and AWS Acceptance

**Files:**
- Modify: `infra/aws/README.md`
- Modify: `docs/superpowers/plans/2026-08-02-aws-integration-roadmap.md`

**Interfaces:** Produces a verified backend API contract for the later frontend UploadEngine.

- [ ] **Step 1: Run `tests/aws_ingestion` and `tests/aws_domain` against PostgreSQL 17** at `127.0.0.1:55432`, plus `manage.py migrate --plan` and `makemigrations --check`.
- [ ] **Step 2: Run private-S3 smoke** beneath one exact `uploads/verification/browser-ingestion-{uuid}/` prefix: initiate Multipart, presign/upload/reconcile, abort it, then prove no object or Multipart remains.
- [ ] **Step 3: Run security/regression checks** including single-admin tests, credential/signature leak scan and `git diff --check`.
- [ ] **Step 4: Record non-secret evidence**, mark Browser ingestion complete and keep Processing orchestration/frontend pending.
- [ ] **Step 5: Commit:** `git commit -m "docs: record browser ingestion verification"`.

## Completion Gate

- PostgreSQL proves strict FIFO single-upload lease behavior including race and expiry takeover.
- File/HLS bytes go directly to S3; refresh derives progress from S3 evidence.
- Completion is idempotent, creates only `source_verified`, queues processing and never marks Media ready.
- HLS traversal, bomb, external reference, encryption and DRM cases fail safely.
- Cancellation and expiry cleanup use only stored exact Multipart IDs/keys.
- Administrator permission, idempotency, revision and lease headers are enforced.
- Runtime AWS permissions suffice and remain denied for unrelated resources.
- No Cloudflare configuration is required.
