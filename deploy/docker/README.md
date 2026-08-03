# MediaCMS on Docker

See: [Details](../../docs/Docker_deployment.md)

## AWS development queue workers

The official `docker-compose-dev.yaml` includes two separate Celery workers:

- `celery_metadata`: queue `youtube_metadata`, concurrency 2; metadata discovery only.
- `celery_worker`: queue `celery`, concurrency 1; serialized download, S3, subtitle, and MediaConvert imports.

The application image includes the pinned `yt-dlp` package and Deno runtime used
by YouTube metadata/import jobs in headless containers. No host Python or browser
installation is required for these workers.

Create the local AWS environment file from the template and replace the account-specific values:

```bash
cp .env.aws-test.example .env.aws-test
docker compose -f docker-compose-dev.yaml up -d --build
docker compose -f docker-compose-dev.yaml ps
docker compose -f docker-compose-dev.yaml logs -f celery_metadata celery_worker
```

The environment file is intentionally ignored from version control because it contains AWS account and role configuration.
