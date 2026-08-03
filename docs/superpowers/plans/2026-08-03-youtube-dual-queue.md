# YouTube Dual Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate concurrent YouTube metadata discovery from the serialized import pipeline and run the local test stack in Docker.

**Architecture:** Job creation dispatches a dedicated `discover_youtube_metadata` task to `youtube_metadata`; this task persists metadata and subtitle options without taking `ProcessingLease`. `Start import` sets `import_requested=true`, and only then does `reconcile_aws_processing` include the job in the single import queue.

**Tech Stack:** Django REST Framework, Celery, Redis, PostgreSQL 17, Docker Compose, yt-dlp, existing AWS gateways.

## Global Constraints

- Metadata discovery may run concurrently.
- Import/download/S3/MediaConvert remains strictly single-task.
- Metadata discovery must not download video bytes or write media objects to S3.
- `Start import` is the only transition into the serialized import queue.
- Local acceptance uses Docker services.

---

### Task 1: Add metadata task and queue eligibility

**Files:**
- Modify: `files/services/processing_queue.py`
- Modify: `files/processing_tasks.py`
- Modify: `files/views/youtube.py`
- Modify: `files/urls.py`
- Test: `tests/aws_domain/test_youtube_subtitles.py`

- [ ] Write tests proving metadata jobs are excluded from `acquire_head_job`, metadata task writes metadata without an S3 call, and Start import makes a job eligible.
- [ ] Run the focused tests and confirm failure.
- [ ] Implement `discover_youtube_metadata(job_id)` with row locking, a metadata checkpoint, discovered metadata, subtitle options, and safe error classification; dispatch it to `youtube_metadata` from job creation.
- [ ] Update queue selection to exclude YouTube jobs without `source_metadata.import_requested`.
- [ ] Run focused tests and confirm pass.
- [ ] Commit: `feat: separate youtube metadata queue`.

### Task 2: Preserve import checkpoint reuse and cancellation

**Files:**
- Modify: `files/services/processing_queue.py`
- Modify: `files/services/processing_runner.py`
- Modify: `files/services/youtube_import.py`
- Test: `tests/aws_domain/test_processing_queue.py`
- Test: `tests/aws_domain/test_youtube_subtitles.py`

- [ ] Add tests proving Start import reuses the metadata checkpoint and that an expired import lease does not prevent metadata discovery.
- [ ] Run tests to verify failure.
- [ ] Ensure import acquisition creates a queued attempt only after `import_requested=true`; keep selected subtitle languages and existing download/S3/MediaConvert checkpoints intact.
- [ ] Verify cancellation releases the import lease and does not delete metadata.
- [ ] Run focused and queue tests.
- [ ] Commit: `feat: reuse youtube metadata for serialized import`.

### Task 3: Docker test topology

**Files:**
- Create or modify: `docker-compose.aws-test.yml`
- Create or modify: `deploy/docker/entrypoint-metadata-worker.sh`
- Create or modify: `deploy/docker/entrypoint-import-worker.sh`
- Modify: `.env.example` or test environment documentation
- Test: `docs/superpowers/plans/2026-08-03-youtube-dual-queue.md`

- [ ] Add services for Django, PostgreSQL, Redis, Celery beat, metadata worker (`--queue youtube_metadata --concurrency 2`), and import worker (`--queue celery --concurrency 1`).
- [ ] Configure health checks and shared environment variables without committing credentials.
- [ ] Start the stack with `docker compose -f docker-compose.aws-test.yml up -d`.
- [ ] Verify both workers report their intended queues and Django health endpoints return 200.
- [ ] Commit: `test: run aws acceptance stack in docker`.

### Task 4: Frontend acceptance flow

**Files:**
- Modify: `frontend/src/static/js/components/youtube/YouTubeImportPanel.jsx`
- Modify: `frontend/src/static/js/components/youtube/YouTubeMetadataCard.jsx`
- Test: `frontend/src/static/js/components/youtube/YouTubeImportPanel.test.jsx`

- [ ] Add tests for metadata-ready rendering, subtitle selection, and Start import request.
- [ ] Run the new tests to confirm failure.
- [ ] Keep metadata polling independent from import progress and show a clear queue state after Start import.
- [ ] Run the full frontend suite.
- [ ] Commit: `feat: show dual-queue youtube import flow`.

### Task 5: End-to-end Docker verification

- [ ] Start the Docker test stack.
- [ ] Upload cookies and create two YouTube metadata jobs; verify both can reach metadata-ready without waiting for an import job.
- [ ] Start one import and verify the second import remains queued.
- [ ] Cancel the active import and verify the next import can acquire the lease.
- [ ] Run backend and frontend full suites.
- [ ] Record observed commands and results in the handoff document.

