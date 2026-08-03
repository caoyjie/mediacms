# MediaCMS Production Image and Deployment Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前从测试机器迁移来的 MediaCMS 代码，建立为本机唯一的统一开发、调试和生产运行基线，并以可追溯镜像完成生产部署。

**Architecture:** CI 在 GitHub Actions 中构建 `base` 和 `full` 两个不可变 GHCR 镜像；生产机只拉取已审核的镜像标签，不在生产机源码挂载运行。Django、Celery 和 Redis/PostgreSQL 保留在 Docker Compose 中，媒体原始文件、转码输入输出和私有播放交给生产 AWS `prod` 栈的 S3、MediaConvert 和 CloudFront。生产机只执行轻量 API、队列和状态协调任务。

**Tech Stack:** Docker Compose v2, GitHub Actions, GHCR, Django 5.2, PostgreSQL 17, Redis, Celery 5.4, AWS CloudFormation, S3, MediaConvert, CloudFront, Cloudflare Tunnel.

## Global Constraints

- 发布基线固定为当前分支 `feat/aws-backend-integration` 的 commit `bed7a63`，禁止生产使用浮动 `latest`。
- 生产机当前约 4 GiB RAM，历史记录有 OOM；禁止在生产机运行本地 FFmpeg 重转码和并发媒体任务。
- 生产 AWS 资源必须使用 `prod` 环境命名和独立 bucket/stack；不得复用 `mediacms-dev` 数据、凭证或测试 bucket。
- 不删除未知用途的 Docker 卷；任何数据库迁移前必须确认数据库来源、备份和恢复点。
- Secrets、AWS credentials、YouTube cookies、Django `SECRET_KEY` 只能存放在主机受保护路径或 GitHub Secrets，不提交仓库。
- 生产切换前必须有可验证的回滚镜像、数据库备份、Compose 配置备份和域名/隧道回滚记录。

## Current Findings

- 仓库当前分支是 `feat/aws-backend-integration`，远端跟踪同名分支；`main` 是 `a505d5d`，两者不能混用作为生产基线。
- 最新提交已包含 GHCR 发布工作流、yt-dlp/Deno Worker 依赖、YouTube 队列拆分和 AWS MediaConvert/S3 集成。
- `.github/workflows/docker-build-push.yml` 已构建 `base`/`full`，但 Compose 仍引用 `mediacms/mediacms:latest`，尚未形成生产不可变标签闭环。
- `docker-compose.yaml` 将源码目录挂载进容器、数据库密码为默认值、媒体本地持久化边界不完整，不能直接作为正式生产编排文件。
- 现有 `.env.aws-test.example` 明确是 `DEVELOPMENT_MODE=True`、`AWS_ENVIRONMENT=dev`，不可复制为生产环境文件。
- 当前 Docker 实际运行项目只有 Mattermost 和 Metabase；没有 MediaCMS 容器。`media-platform_postgres-data` 卷存在但当前未挂载，不能假设它是 MediaCMS 数据。
- 主机磁盘约 100 GiB、可用约 43 GiB；内存约 3.8 GiB 可用约 1 GiB，且既有 OOM 记录，镜像构建应在 CI 完成。
- AWS 文档记录 `mediacms-dev` 栈已完成验证，但生产栈、生产域名、Cloudflare ACM/DNS gate 和生产运行凭证尚未确认。

---

### Task 1: Freeze source and inspect existing production data

**Files:**
- Read: `git status`, `git branch -avv`, `docs/superpowers/handoff/2026-08-02-aws-integration-handoff.md`
- Read: `ubuntu-production-environment.txt`
- Read: `docker-compose.yaml`, `docker-compose/docker-compose-named-volumes.yaml`
- Produce: `/etc/mediacms/release/<timestamp>/inventory.txt`

- [ ] **Step 1: Record the release commit and dirty files.**

```bash
git rev-parse HEAD
git status --short --branch
git diff --check
```

Expected: release commit is `bed7a63`; any dirty file is explicitly classified as release input or excluded.

- [ ] **Step 2: Inventory Docker projects, ports, volumes and reverse proxy ownership.**

```bash
docker compose ls --all
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker volume ls
ss -lntup
```

Expected: identify who owns ports 80/443/5432/6379 and whether any existing database or media volume is connected to MediaCMS.

- [ ] **Step 3: Preserve evidence before touching containers.**

Save `docker inspect` output for all candidate MediaCMS containers/volumes, PostgreSQL version/database list, and current proxy/tunnel configuration. Do not run `down`, `rm`, `volume prune`, or `docker system prune`.

- [ ] **Step 4: Decide database mode.**

Use one of two explicit outcomes: `new-production-database` with an empty schema, or `migrated-database` with a verified dump/restore. If the `media-platform` volume cannot be proven to contain MediaCMS data, treat it as unrelated and leave it untouched.

### Task 2: Make production configuration explicit

**Files:**
- Create: `docker-compose.production.yaml`
- Create: `.env.production.example`
- Modify: `deploy/docker/local_settings.py`
- Read: `deploy/docker/entrypoint.sh`, `deploy/docker/prestart.sh`, `infra/aws/README.md`

- [ ] **Step 1: Define production variables without test defaults.**

The production env contract must include `FRONTEND_HOST`, `PORTAL_NAME`, `POSTGRES_*`, `REDIS_LOCATION`, `AWS_REGION`, `AWS_DEFAULT_REGION`, `AWS_ENVIRONMENT=prod`, `AWS_MEDIA_BUCKET`, `AWS_MEDIACONVERT_ROLE_ARN`, `AWS_MEDIACONVERT_VIDEO_TEMPLATE`, `AWS_MEDIACONVERT_AUDIO_TEMPLATE`, `SECRET_KEY`, and the approved CloudFront playback settings.

- [ ] **Step 2: Split services by responsibility.**

Compose must define a one-shot `migrations` service, one `web` service, one `celery_beat`, one `celery_worker` for normal jobs, one `celery_metadata` for YouTube metadata, PostgreSQL, Redis, and the selected reverse-proxy integration. Disable duplicate built-in services with `ENABLE_*` flags.

- [ ] **Step 3: Remove source-code bind mounts from production app services.**

Use the image’s application files as shipped. Persist only PostgreSQL data, Redis data if required, logs, and the explicitly approved local runtime directories. Media should use the AWS path configured by settings; do not silently fall back to `media_files` for production media.

- [ ] **Step 4: Add health checks and resource limits.**

Keep worker concurrency at one, cap worker memory so OOM does not take down PostgreSQL, add service health checks, and make migrations a dependency of web/workers. Ensure the Compose project name is distinct from Mattermost/Metabase.

### Task 3: Build and publish the release images in CI

**Files:**
- Modify: `.github/workflows/docker-build-push.yml`
- Read: `Dockerfile`, `.dockerignore`
- Test: `tests/backend_image.test.sh`, `tests/mediacms_image.test.sh` if present

- [ ] **Step 1: Run repository checks before publishing.**

```bash
deploy/scripts/test_environment_probes.sh
deploy/scripts/test_aws_infrastructure_scripts.sh
```

Run the backend/frontend test commands documented by the current handoff. Do not publish on a red baseline.

- [ ] **Step 2: Choose immutable image tags.**

Publish both targets as `ghcr.io/caoyjie/mediacms:<git-sha>` and `ghcr.io/caoyjie/mediacms:<approved-release-tag>`, plus matching `-full` tags. Production Compose must reference the SHA or release tag, never `latest`.

- [ ] **Step 3: Build in GitHub Actions, not on the production VM.**

Trigger the existing workflow manually for the release commit, verify both image manifests and digests, and record the digest in the release inventory. Confirm the package visibility and that the production host can authenticate to GHCR with a read-only token.

- [ ] **Step 4: Verify runtime contents.**

Run a disposable container from the exact digest and verify Python/Django, FFmpeg/ffprobe, Bento4, Deno, yt-dlp, Celery, and the expected entrypoint. The `full` image is only needed for explicitly enabled Whisper/full-worker features.

### Task 4: Provision and validate AWS production resources

**Files:**
- Use: `infra/aws/mediacms-core.yaml`
- Use: `deploy/scripts/validate_aws_infrastructure.sh`
- Use: `deploy/scripts/create_aws_change_set.sh`
- Use: `deploy/scripts/describe_aws_change_set.sh`
- Use: `deploy/scripts/extract_runtime_aws_env.sh`
- Read: `infra/aws/README.md`

- [ ] **Step 1: Validate templates and identity.**

Run repository tests, `cfn-lint`, CloudFormation template validation, and `aws sts get-caller-identity` with the approved `default` profile in `us-east-1`.

- [ ] **Step 2: Create a reviewed `mediacms-prod` change set.**

Use production parameters for environment, application origin, bucket, MediaConvert templates, and optional custom domain. Review all IAM, S3, CloudFront, Secrets Manager and MediaConvert actions; execution creates long-lived access keys and requires explicit approval.

- [ ] **Step 3: Execute and capture non-secret outputs.**

Record bucket, role, template, distribution, key group and runtime secret identifiers. Extract only the active runtime credentials to `/etc/mediacms/secrets/aws-runtime.env` with root ownership and mode `0640`; never print the values.

- [ ] **Step 4: Run positive and negative AWS checks.**

Verify runtime access to the production bucket prefixes and MediaConvert templates, CloudFront private playback, and denial of unrelated bucket keys, IAM management, CloudFormation, Secrets Manager retrieval, and unsigned media access.

### Task 5: Deploy and perform the production cutover

**Files:**
- Use: `docker-compose.production.yaml`
- Use: `.env.production`
- Use: `deploy/docker/prestart.sh`
- Create: `/etc/mediacms/compose/docker-compose.production.yaml`
- Create: `/etc/mediacms/release/<release>/rollback.env`

- [ ] **Step 1: Back up before migration.**

Create a PostgreSQL custom-format dump, record its checksum, back up the current production env/proxy/tunnel configuration, and verify the dump can be listed. Do not migrate until the restore test is successful or the database is confirmed new and disposable.

- [ ] **Step 2: Install protected runtime files.**

Place the production env and AWS runtime env outside Git, set owner/group/mode according to the host service account, and verify that no test variable (`DEVELOPMENT_MODE=True`, `AWS_ENVIRONMENT=dev`, default database password) is present.

- [ ] **Step 3: Pull and start the exact image digest.**

Authenticate to GHCR, pull the recorded `base` or `full` digest, render and review `docker compose -f docker-compose.production.yaml config`, then start PostgreSQL and Redis first. Start migrations as a one-shot job and inspect its exit code before starting web/workers.

- [ ] **Step 4: Start lightweight services and proxy.**

Start web, beat, normal worker, metadata worker and proxy. Confirm all services use the same `SECRET_KEY`, database, Redis, `AWS_ENVIRONMENT=prod` and image digest. Confirm no container exposes PostgreSQL or Redis publicly.

- [ ] **Step 5: Run application smoke checks.**

Run `manage.py check`, authenticated admin login, static asset load, health endpoint, one small browser upload, S3 promotion, one serialized MediaConvert video canary, private CloudFront playback, and cleanup verification. Also check YouTube metadata only if production cookies were separately approved.

### Task 6: Cutover, monitoring and rollback

**Files:**
- Read: `docs/admins_docs.md`, `infra/aws/README.md`
- Produce: `/etc/mediacms/release/<release>/cutover-report.md`

- [ ] **Step 1: Switch traffic only after canary success.**

Update the external reverse proxy or Cloudflare Tunnel route, then verify HTTPS, `FRONTEND_HOST`, CORS, CSRF, signed CloudFront cookies, upload callbacks and log collection from an external client.

- [ ] **Step 2: Observe the first production window.**

Monitor Docker health, memory/swap/OOM events, PostgreSQL connections, Redis queue depth, Celery failures, MediaConvert reconciliation, S3 cleanup and CloudFront 4xx/5xx. Keep concurrency one until real load data justifies change.

- [ ] **Step 3: Roll back on defined triggers.**

Rollback if migrations/checks fail, data is missing, private playback remains 403 after one controlled refresh, canary processing cannot reconcile, duplicate active versions appear, or memory pressure threatens PostgreSQL. Restore the previous image/config and proxy route; do not delete AWS production resources during application rollback.

- [ ] **Step 4: Close the release.**

Record image digests, Compose config checksum, database backup checksum, AWS stack/change-set name, canary IDs, observed resource usage and rollback status. Only after sign-off may unused test resources be cleaned up using exact resource identifiers.

## Release Gates

1. No unresolved data-source or port ownership ambiguity.
2. Production Compose and env render without test values or source mounts.
3. CI images pass tests and are referenced by immutable digest.
4. `mediacms-prod` AWS change set is reviewed and explicitly approved.
5. Database backup/restore and migration result are verified.
6. One video canary reaches ready state and plays privately through CloudFront.
7. Memory/OOM behavior and rollback are documented before declaring cutover complete.

