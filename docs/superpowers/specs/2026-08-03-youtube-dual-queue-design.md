# YouTube Metadata and Import Queue Separation

## Goal

Allow YouTube metadata discovery to run concurrently without consuming the single import-processing slot. Only `Start import` enters the serialized queue that performs download, S3 upload, subtitles, MediaConvert, publication, and cleanup.

## Queue boundaries

### Metadata discovery queue

`YouTubeJobCreateView` creates the job and dispatches `discover_youtube_metadata(job_id)` to the `youtube_metadata` queue. This task may run concurrently with other metadata tasks. It performs yt-dlp extraction with `download=False`, persists the metadata checkpoint and subtitle options, and leaves the job at `stage=metadata_ready` with `import_requested=false`.

Metadata discovery does not acquire `ProcessingLease`, download media, upload S3 objects, fetch subtitle payloads, or call MediaConvert.

### Serialized import queue

`Start import` persists `import_requested=true`, selected subtitle languages, and `status=queued`. The existing `ProcessingLease` queue only considers YouTube jobs whose `import_requested` flag is true. It then performs download, S3 upload, subtitle retrieval/publication, MediaConvert, output verification, activation, and cleanup with one active import worker.

Cancellation applies independently: metadata tasks can be canceled without affecting import capacity; import tasks can be canceled through the existing cancellation flow.

## State and recovery

The existing job status fields remain unchanged. Queue eligibility is derived from source type and `source_metadata.import_requested`. Metadata attempts are persisted as queued/completed attempts so Start import can reuse the metadata checkpoint; the serialized queue creates or reuses the next queued attempt for import.

Metadata errors are persisted as `failed/action_required` or retryable failure without creating an import lease. A stale import lease is recoverable by the existing lease takeover logic and never blocks metadata discovery.

## Local Docker test environment

Run Django, metadata worker, serialized import worker, Celery beat, Redis, and PostgreSQL through Docker Compose. The metadata worker uses bounded concurrency (default 2); the import worker uses concurrency 1. AWS credentials and test settings are injected through the existing environment file, never committed.

## API and UI contract

- `POST /api/v1/aws/youtube/jobs/`: create job and enqueue metadata discovery.
- `GET /api/v1/aws/youtube/jobs/<id>/`: returns metadata, subtitle options, and stage.
- `POST /api/v1/aws/youtube/jobs/<id>/start/`: selects subtitles and enters serialized import queue.

The Add Media page displays metadata and subtitle choices as soon as discovery completes. It displays `Start import` only at `metadata_ready`; no import-side progress starts before that action.

