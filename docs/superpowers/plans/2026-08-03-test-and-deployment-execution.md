# AWS MediaCMS Test and Deployment Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate the AWS-backed MediaCMS flow end-to-end in an isolated AWS test stack, then deploy the lightweight application to the resource-constrained production VM with a documented rollback path.

**Architecture:** PostgreSQL remains the authoritative application state store. The VM handles Django, Celery scheduling, metadata, and short orchestration ticks; S3 and MediaConvert handle large media and transcoding; CloudFront serves private playback through signed cookies. Cloudflare Tunnel is required only for the final external-domain path and is not a prerequisite for local or AWS-hostname acceptance.

**Tech Stack:** AWS CLI v2 (default profile), CloudFormation, S3, IAM, Secrets Manager, MediaConvert, CloudFront, Docker Compose, PostgreSQL 17, Redis, Django/pytest, React/Jest, yt-dlp with Deno and yt-dlp-ejs.

## Global Constraints

- Use a dedicated test stack and bucket prefix; do not reuse historical MediaCMS databases or media.
- Region is `us-east-1`; the top-level S3 bucket name is `mediacms` where the AWS account naming rules permit it, otherwise use the stack-generated account/region name documented by CloudFormation.
- Use AWS CLI `default` profile; never commit credentials, cookies, private keys, or signed-cookie material.
- Production VM remains at 4 vCPU / 8 GiB; do not run FFmpeg/MediaConvert-equivalent heavy jobs on it.
- Only one ingestion/processing task may run at a time; additional work waits in the PostgreSQL-backed queue.
- CloudWatch alarms are out of MVP scope; abnormal MediaConvert jobs are detected through API reconciliation and task-center status.
- Every destructive test cleanup requires an explicit stack/resource identifier and a confirmation log.

---

### Task 1: Freeze the local baseline

**Files:**
- Read: `deploy/scripts/run_backend_tests.sh`
- Read: `docs/superpowers/handoff/2026-08-02-aws-integration-handoff.md`

- [ ] **Step 1: Start or verify local dependencies**

```bash
docker ps --filter name=mediacms-aws-test-postgres
docker ps --filter name=mediacms-redis-1
```

Expected: PostgreSQL is `healthy`; Redis is running.

- [ ] **Step 2: Run backend and frontend regression**

```bash
deploy/scripts/run_backend_tests.sh
(cd frontend && npm test -- --runInBand)
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 \
POSTGRES_NAME=mediacms_test POSTGRES_USER=mediacms_test \
POSTGRES_PASSWORD=mediacms_test_local_only \
.venv/bin/python manage.py check
```

Expected baseline: backend `426 passed`, frontend `207 passed`, Django check has no issues.

- [ ] **Step 3: Record the baseline**

Save command output and the current commit SHA in the release notes. Do not proceed to AWS provisioning if this baseline is red.

---

### Task 2: Validate infrastructure templates without provisioning

**Files:**
- Read: `infra/aws/mediacms-core.yaml`
- Read: `infra/aws/mediacms-certificate.yaml`
- Test: `tests/aws_infrastructure/`
- Run: `deploy/scripts/validate_aws_infrastructure.sh`

- [ ] **Step 1: Run repository contract tests**

```bash
.venv/bin/pytest tests/aws_infrastructure -q
```

- [ ] **Step 2: Run static template validation**

```bash
deploy/scripts/validate_aws_infrastructure.sh
cfn-lint infra/aws/mediacms-core.yaml infra/aws/mediacms-certificate.yaml
```

- [ ] **Step 3: Validate with AWS using the default profile**

```bash
aws sts get-caller-identity --profile default
aws cloudformation validate-template \
  --template-body file://infra/aws/mediacms-core.yaml \
  --profile default --region us-east-1
```

Expected: account identity is the intended test account and all validation commands pass. No stack is created by this task.

---

### Task 3: Create the isolated AWS test stack

**Files:**
- Use: `deploy/scripts/create_aws_change_set.sh`
- Use: `deploy/scripts/describe_aws_change_set.sh`
- Use: `deploy/scripts/extract_runtime_aws_env.sh`
- Read: `docs/superpowers/specs/aws-backend-integration/02-aws-infrastructure-and-storage.md`

- [ ] **Step 1: Confirm account, region, and stack name**

```bash
aws sts get-caller-identity --profile default
aws configure get region --profile default
```

Set `AWS_REGION=us-east-1` explicitly for all subsequent commands. Use the test stack name `mediacms-dev` unless an existing stack with that name is found.

- [ ] **Step 2: Create and inspect the change set**

```bash
AWS_PROFILE=default AWS_REGION=us-east-1 \
deploy/scripts/create_aws_change_set.sh mediacms-dev
AWS_PROFILE=default AWS_REGION=us-east-1 \
deploy/scripts/describe_aws_change_set.sh mediacms-dev
```

Review IAM, bucket, MediaConvert role, CloudFront OAC/key group, and Secrets Manager resources before execution.

- [ ] **Step 3: Execute only after review**

```bash
aws cloudformation execute-change-set \
  --stack-name mediacms-dev \
  --change-set-name <reviewed-change-set-arn> \
  --profile default --region us-east-1
aws cloudformation wait stack-create-complete \
  --stack-name mediacms-dev --profile default --region us-east-1
```

- [ ] **Step 4: Export runtime outputs without exposing secrets**

```bash
AWS_PROFILE=default AWS_REGION=us-east-1 \
deploy/scripts/extract_runtime_aws_env.sh mediacms-dev > /tmp/mediacms-dev-runtime.env
chmod 600 /tmp/mediacms-dev-runtime.env
```

Do not paste this file into chat, Git, CI logs, or issue trackers.

---

### Task 4: Run AWS media acceptance fixtures

**Files:**
- Read: `docs/superpowers/specs/aws-backend-integration/07-deployment-and-acceptance.md`
- Read: `docs/superpowers/specs/aws-backend-integration/10-test-and-deployment-plan.md`
- Fixtures: `/home/caoyujie/Videos/fitness/陈康/005.胸部-双杠臂屈伸动作讲解.mp4`, `/home/caoyujie/Videos/Marine英语课/新录音.mp3`

- [ ] **Step 1: Produce minimal local fixtures**

Create short clips only in a disposable directory; retain the originals unchanged:

```bash
mkdir -p /tmp/mediacms-fixtures
ffmpeg -y -i '/home/caoyujie/Videos/fitness/陈康/005.胸部-双杠臂屈伸动作讲解.mp4' \
  -t 12 -c copy /tmp/mediacms-fixtures/video.mp4
ffmpeg -y -i '/home/caoyujie/Videos/Marine英语课/新录音.mp3' \
  -t 12 -c copy /tmp/mediacms-fixtures/audio.mp3
```

- [ ] **Step 2: Submit exactly one video job**

Upload through the Add Media flow, observe byte/object progress, leave and re-open the page, then confirm the task center resumes the same task rather than creating a second attempt.

- [ ] **Step 3: Verify video outputs**

Confirm PostgreSQL checkpoints, MediaConvert job status, candidate manifest verification, atomic active-version switch, poster/thumbnail, HLS variants, and cleanup of temporary backend files.

- [ ] **Step 4: Submit exactly one audio job**

Confirm audio HLS playback and metadata behavior. Verify that no video-only poster requirement blocks readiness.

- [ ] **Step 5: Import an HLS ZIP fixture**

Generate a short HLS fixture with FFmpeg, upload the manifest and segments through the browser path, and verify manifest validation without a full MediaConvert transcode.

---

### Task 5: Verify YouTube and subtitle behavior

**Files:**
- Read: `docs/superpowers/specs/aws-backend-integration/05-youtube-and-subtitles.md`
- Use: `deploy/scripts/smoke_browser_upload.py`

- [ ] **Step 1: Validate the worker runtime**

```bash
.venv/bin/yt-dlp --version
deno --version
.venv/bin/yt-dlp --no-playlist --skip-download --dump-single-json \
  --js-runtimes deno --cookies /secure/ytb_cookies.txt \
  'https://www.youtube.com/watch?v=<short-fixture-id>' >/tmp/youtube-check.json
```

- [ ] **Step 2: Test metadata-first import**

Confirm the title defaults from metadata, is visible in the form/task center, and remains editable before publication.

- [ ] **Step 3: Test cookie lifecycle**

Verify last-upload date, missing-cookie warning, retry/resume after cookie upload, and default reuse of the last valid cookie. Never expose cookie contents in logs or API responses.

- [ ] **Step 4: Test subtitle classification**

Accept all three outcomes: original English/Chinese plus bilingual WebVTT; English-only; or unavailable subtitles with a clear frontend message. A subtitle failure must not fail an otherwise valid video.

---

### Task 6: Verify private playback and CloudFront boundary

**Files:**
- Read: `docs/superpowers/specs/aws-backend-integration/06-cloudfront-playback.md`
- Test: `tests/api/`, `tests/aws_domain/`

- [ ] **Step 1: Run API authorization tests**

```bash
deploy/scripts/run_backend_tests.sh tests/api tests/aws_domain -q
```

- [ ] **Step 2: Bootstrap media cookies on protected-page entry**

Open the media list and verify poster/thumbnail requests succeed before playback. Confirm a 403 causes one single-flight refresh and the image recovers without a page reload.

- [ ] **Step 3: Verify playback controls**

Confirm HLS playback, quality selection, subtitles, audio-only mode, seek persistence, and resume after reload for the active asset version.

- [ ] **Step 4: Execute Cloudflare gate only when domain data is available**

Configure Tunnel route, DNS, HTTPS mode, CORS origin, and CloudFront ACM validation in the Cloudflare/AWS consoles. Until then, use the CloudFront distribution hostname and record the gate as pending.

---

### Task 7: Deploy the lightweight application to the production VM

**Files:**
- Read: `deploy/docker/README.md`
- Read: `docs/superpowers/specs/aws-backend-integration/07-deployment-and-acceptance.md`
- Use: `deploy/scripts/build_and_deploy.sh`

- [ ] **Step 1: Capture a production backup and inventory**

Record the current compose project, image digests, database backup location, mounted volumes, and active Cloudflare Tunnel configuration. Do not remove old containers yet.

- [ ] **Step 2: Prepare protected runtime configuration**

Install only lightweight runtime dependencies. Set AWS region, stack output identifiers, database URL, Redis URL, CloudFront distribution/key-group values, yt-dlp/Deno paths, and the single-admin setting in a root-readable protected env file (`chmod 600`).

- [ ] **Step 3: Deploy application services**

Deploy Django, Celery beat/short/long workers, and reverse proxy. Keep worker concurrency at one and ensure no local FFmpeg transcoding service is enabled.

- [ ] **Step 4: Run clean migration and smoke checks**

```bash
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py check
docker compose ps
```

Verify the singleton administrator and that old media is not visible in the new database.

- [ ] **Step 5: Run one production canary**

Use a short fixture, confirm task-center progress, private playback, cleanup, and API-based reconciliation. Stop the canary before importing additional media.

---

### Task 8: Rollback and test-resource cleanup

**Files:**
- Read: `docs/superpowers/specs/aws-backend-integration/07-deployment-and-acceptance.md`

- [ ] **Step 1: Define rollback trigger**

Rollback if migration/check fails, private playback cannot recover from 403, a MediaConvert job cannot reconcile, or the canary creates duplicate active asset versions.

- [ ] **Step 2: Roll back application only**

Restore the previous image/configuration and restart the compose project. Do not delete the AWS stack during an application rollback.

- [ ] **Step 3: Clean the isolated AWS test stack after sign-off**

```bash
aws cloudformation delete-stack --stack-name mediacms-dev \
  --profile default --region us-east-1
aws cloudformation wait stack-delete-complete \
  --stack-name mediacms-dev --profile default --region us-east-1
```

First verify that the bucket is empty or that the stack's retention policy is intentional. Record deleted resource IDs and any retained artifacts.

---

## Exit criteria

- Local backend/frontend baseline is green.
- CloudFormation templates pass repository tests, `cfn-lint`, and AWS validation.
- Video, audio, HLS import, and YouTube canary flows complete with one active task at a time.
- MediaConvert status is reconciled through the API; cleanup completes without retaining backend media files.
- Private CloudFront playback, subtitles, quality selection, poster/thumbnail recovery, and resume work.
- Production VM runs only lightweight services and has a tested rollback path.
- Test AWS resources are deleted or explicitly retained with owner, reason, and cost recorded.
