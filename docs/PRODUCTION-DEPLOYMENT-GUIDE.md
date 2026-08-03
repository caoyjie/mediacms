# MediaCMS Production Deployment Guide

**Release Baseline:** `bed7a63` on branch `feat/aws-backend-integration`  
**Date:** 2026-08-03  
**Plan:** `docs/superpowers/plans/2026-08-03-production-image-and-deployment.md`

---

## Overview

This guide walks you through deploying MediaCMS to production using:
- Docker Compose for application services
- GHCR for immutable container images
- AWS CloudFormation for media infrastructure (S3, MediaConvert, CloudFront)
- Manual execution via Makefile targets

**Total: 6 Tasks, ~35 steps**

---

## Prerequisites

- Root access to production host
- Docker and Docker Compose v2 installed
- AWS CLI configured with `default` profile in `us-east-1`
- GitHub personal access token for GHCR (read packages permission)
- Git repository at `/root/project/mediacms` on `feat/aws-backend-integration`

---

## Task 1: Inspection ✅ COMPLETED

Already completed. Evidence saved at `/etc/mediacms/release/20260803-205435/`

**Key findings:**
- Release commit: `bed7a63` ✓
- Database mode: `new-production-database` (clean slate)
- Port 443: owned by xray (reverse proxy needed)
- Memory: 3.8 GiB total, ~1 GiB available (OOM history)

---

## Task 2: Configuration ✅ COMPLETED

Production configuration files created:
- `docker-compose.production.yaml` - Service definitions
- `.env.production.example` - Environment template

**Verify:**
```bash
make -f Makefile.production task2-validate
```

---

## Task 3: Build and Publish Container Images

### Step 3.1: Run Repository Tests

```bash
cd /root/project/mediacms
make -f Makefile.production task3-run-tests
```

**Expected output:** All tests pass

**If tests fail:** Fix issues before proceeding

---

### Step 3.2: Trigger GitHub Actions Build

```bash
make -f Makefile.production task3-trigger-build
```

This prints instructions. You need to:

1. Open browser: https://github.com/caoyjie/mediacms/actions/workflows/docker-build-push.yml
2. Click "Run workflow"
3. Select branch: `feat/aws-backend-integration`
4. Verify commit shows: `bed7a63`
5. Click "Run workflow" button
6. Wait for build to complete (~10-15 minutes)

**Expected artifacts:**
- `ghcr.io/caoyjie/mediacms:bed7a63`
- `ghcr.io/caoyjie/mediacms:bed7a63-full`

---

### Step 3.3: Verify Published Images

```bash
make -f Makefile.production task3-verify-images
```

**Expected output:** Both images pulled successfully with digests displayed

**If pull fails:** Check GHCR package visibility (should be public or you need auth)

---

### Step 3.4: Test Runtime Contents

```bash
make -f Makefile.production task3-test-runtime
```

**Expected output:** Python, ffmpeg, deno, yt-dlp, celery versions displayed

---

**Task 3 Complete ✓** Images are built and verified

---

## Task 4: Provision AWS Production Resources

### Step 4.1: Validate CloudFormation Templates

```bash
cd /root/project/mediacms
make -f Makefile.production task4-validate
```

**Expected output:** 
- Templates validated ✓
- AWS identity displayed (verify correct account)

**If validation fails:** Check AWS CLI configuration

---

### Step 4.2: Create Change Set (Review Only)

⚠️ **BEFORE RUNNING:** Edit `deploy/scripts/create_aws_change_set.sh` and verify parameters:
- `STACK_NAME=mediacms-prod`
- `ENVIRONMENT=prod`
- `AWS_REGION=us-east-1`

```bash
make -f Makefile.production task4-create-changeset
```

**Expected output:** Change set created, ID displayed

---

### Step 4.3: Review Change Set

```bash
make -f Makefile.production task4-describe-changeset
```

**CRITICAL REVIEW:** This will create:
- S3 bucket for media storage
- MediaConvert job templates
- CloudFront distribution with signed URL support
- IAM role and access keys
- Secrets Manager entries

**Review ALL changes carefully.** Look for:
- Bucket names contain `prod` (not `dev`)
- IAM permissions are minimal
- No unintended resource modifications

---

### Step 4.4: Execute Change Set

⚠️ **WARNING:** This creates real AWS resources and may incur costs

```bash
make -f Makefile.production task4-execute
```

**Confirmation required:** Type `EXECUTE` when prompted

**Wait time:** ~5-10 minutes for stack creation

**Expected output:** Stack create complete

**If execution fails:** Check CloudFormation console for error details

---

### Step 4.5: Extract Runtime Credentials

```bash
make -f Makefile.production task4-extract-outputs
```

**Expected output:** Credentials saved to `/etc/mediacms/secrets/aws-runtime.env` with mode 0640

**What this does:**
- Reads stack outputs
- Extracts AWS access key, secret key, bucket name, role ARN
- Saves to protected file location

⚠️ **NEVER commit this file to git**

---

### Step 4.6: Verify AWS Access

```bash
make -f Makefile.production task4-verify-access
```

**Expected output:** Positive and negative access checks pass

**Positive checks:** Can write to production S3 bucket, create MediaConvert jobs
**Negative checks:** Cannot access dev buckets, cannot modify IAM/CloudFormation

---

**Task 4 Complete ✓** AWS production stack is live

---

## Task 5: Deploy Production Services

### Step 5.1: Create Production Environment File

```bash
cp .env.production.example .env.production
```

**Edit `/root/project/mediacms/.env.production` and set:**

```bash
# Replace REPLACEME with release commit
MEDIACMS_IMAGE=ghcr.io/caoyjie/mediacms:bed7a63

# Set your production domain
FRONTEND_HOST=https://your-actual-domain.com
PORTAL_NAME=Your Media Portal

# Generate strong random secret (50+ characters)
SECRET_KEY=<use: openssl rand -base64 48>

# Set admin credentials
ADMIN_USER=admin
ADMIN_EMAIL=admin@your-domain.com
ADMIN_PASSWORD=<strong random password>

# Set database password
POSTGRES_PASSWORD=<strong random password>

# AWS credentials from Task 4 outputs
AWS_ACCESS_KEY_ID=<from /etc/mediacms/secrets/aws-runtime.env>
AWS_SECRET_ACCESS_KEY=<from /etc/mediacms/secrets/aws-runtime.env>
AWS_MEDIA_BUCKET=<from stack outputs>
AWS_MEDIACONVERT_ROLE_ARN=<from stack outputs>

# CloudFront settings (if using signed URLs)
# AWS_CLOUDFRONT_DISTRIBUTION_ID=<from stack outputs>
# AWS_CLOUDFRONT_KEY_GROUP_ID=<from stack outputs>
```

**Security check:**
```bash
chmod 600 .env.production
grep -E "(changeme|REPLACE|dev)" .env.production  # Should return nothing
```

---

### Step 5.2: Install Protected Configuration

```bash
make -f Makefile.production task5-install-config
```

**Expected output:** 
- Compose file copied to `/etc/mediacms/compose/`
- Environment validation passes (no test values detected)

---

### Step 5.3: Authenticate to GHCR

Export your GitHub personal access token:

```bash
export GHCR_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
```

---

### Step 5.4: Pull Production Images

```bash
make -f Makefile.production task5-pull-images
```

**Expected output:** Images pulled with exact digests

---

### Step 5.5: Start Infrastructure Services

```bash
make -f Makefile.production task5-start-infra
```

**Expected output:** PostgreSQL and Redis started and healthy

**Verify:**
```bash
docker compose -f docker-compose.production.yaml --project-name mediacms-prod ps
```

Should show `postgres` and `redis` as healthy.

---

### Step 5.6: Run Database Migrations

```bash
make -f Makefile.production task5-run-migrations
```

**Expected output:** Migrations complete, admin user created

**This creates:**
- Database schema
- Admin user (from ADMIN_USER/ADMIN_EMAIL/ADMIN_PASSWORD)

**If migrations fail:** Check PostgreSQL logs, verify database credentials

---

### Step 5.7: Start Application Services

```bash
make -f Makefile.production task5-start-services
```

**Expected output:** web, celery_beat, celery_worker, celery_metadata started

**Verify all services are running:**
```bash
docker compose -f docker-compose.production.yaml --project-name mediacms-prod ps
```

All should show status "Up" and healthy.

---

### Step 5.8: Run Smoke Tests

```bash
make -f Makefile.production task5-smoke-test
```

**Automated test:** Django system check

**Manual tests required:**

1. **Admin login test:**
   ```bash
   # Access internally (xray not configured yet)
   curl http://localhost:8080/admin/
   ```
   Should return login page HTML

2. **Health endpoint:**
   ```bash
   curl http://localhost:8080/health
   ```
   Should return `{"status": "ok"}`

3. **Static assets:** Check that CSS/JS loads correctly

4. **Upload test:** 
   - Login to admin: http://localhost:8080/admin/
   - Upload small test video (< 10MB)
   - Verify upload completes

5. **S3 promotion check:**
   ```bash
   docker compose -f docker-compose.production.yaml --project-name mediacms-prod logs celery_worker | grep -i s3
   ```
   Should show S3 upload activity

6. **MediaConvert job:**
   ```bash
   aws mediaconvert list-jobs --region us-east-1 --status PROGRESSING --profile default
   ```
   Should show job created

7. **CloudFront playback:**
   - Wait for MediaConvert job to complete
   - Try playing video through MediaCMS interface
   - Should use CloudFront URL with signed cookies

**If any test fails:** Check logs before proceeding:
```bash
docker compose -f docker-compose.production.yaml --project-name mediacms-prod logs --tail=100
```

---

**Task 5 Complete ✓** Application is running and smoke tests pass

---

## Task 6: Traffic Cutover and Monitoring

### Step 6.1: Configure Reverse Proxy

**Port conflict resolution:** xray owns port 443, MediaCMS web service is on port 8080

**Option A: Configure xray to proxy to MediaCMS**

Edit xray config (location TBD) to add upstream:
```json
{
  "upstream": "http://127.0.0.1:8080",
  "domain": "your-domain.com"
}
```

**Option B: Use Cloudflare Tunnel**

Configure cloudflared to route traffic:
```bash
cloudflared tunnel route dns <tunnel-name> your-domain.com
```

Update tunnel config to point to `http://localhost:8080`

**Option C: Use Tailscale** (if private deployment)

Access directly via Tailscale IP on port 8080

---

### Step 6.2: Switch Traffic

```bash
make -f Makefile.production task6-switch-traffic
```

**This is a manual step.** Follow the instructions printed.

**After switching, verify from external client:**

1. **HTTPS works:**
   ```bash
   curl -I https://your-domain.com
   ```
   Should return 200 OK

2. **FRONTEND_HOST correct:**
   - Check that URLs in responses match your FRONTEND_HOST setting
   - Check CORS headers allow your domain

3. **CSRF protection:**
   - Try logging in from external browser
   - Should work without CSRF errors

4. **CloudFront signed cookies:**
   - Play a video
   - Check network tab: video segments should come from CloudFront domain
   - Should have `Set-Cookie` headers for CloudFront signatures

---

### Step 6.3: Monitor Production

```bash
make -f Makefile.production task6-monitor
```

**Displays:**
- Container health status
- Memory usage per container
- Recent logs (last 50 lines)

**Watch for:**
- OOM kills (memory exceeded)
- PostgreSQL connection errors
- Celery task failures
- MediaConvert job errors
- S3 access denied errors

**Continuous monitoring:**
```bash
# Watch container stats in real-time
docker stats

# Follow logs
make -f Makefile.production logs
```

**Key metrics to watch (first 24 hours):**
- Memory usage stays < 3.5 GB total
- No OOM events in `dmesg`
- PostgreSQL connections < 100
- Redis memory < 200MB
- Celery queue depth < 50
- MediaConvert jobs complete within 2x video duration
- S3 cleanup runs (old temp files deleted)

---

### Step 6.4: Rollback Procedure (if needed)

**Rollback triggers:**
1. Services cannot start
2. Database migrations corrupted
3. Private playback returns 403 after refresh
4. Memory pressure threatens PostgreSQL
5. Duplicate active versions detected

**Execute rollback:**

```bash
make -f Makefile.production task6-rollback
```

**Manual steps after rollback:**
1. Stop all MediaCMS containers (done by make target)
2. Revert reverse proxy configuration to previous state
3. Verify traffic routes to backup system
4. **DO NOT delete AWS production resources** - they can be reused

**Database rollback:** (if needed)
```bash
# Restore from backup
pg_restore -h localhost -U mediacms -d mediacms /path/to/backup.dump
```

---

### Step 6.5: Close Release

```bash
make -f Makefile.production task6-close-release
```

**Creates:** `/etc/mediacms/release/<timestamp>/cutover-report.md`

**Contains:**
- Release commit
- Image digests used
- Compose config checksum
- Timestamp

**Final verification checklist:**

- [ ] All services healthy for 1+ hour
- [ ] Test video uploaded, transcoded, and plays privately
- [ ] No OOM events in system logs
- [ ] Memory usage stable under 3.5 GB
- [ ] External HTTPS access works
- [ ] Admin login works externally
- [ ] CloudFront signed URLs work
- [ ] Celery queues processing normally
- [ ] No errors in application logs

---

**Task 6 Complete ✓** Production cutover successful

---

## Post-Deployment

### Monitoring Commands

```bash
# Check service status
docker compose -f docker-compose.production.yaml --project-name mediacms-prod ps

# View logs
docker compose -f docker-compose.production.yaml --project-name mediacms-prod logs -f

# Check memory usage
docker stats --no-stream

# Check OOM events
dmesg | grep -i "out of memory"

# Check MediaConvert jobs
aws mediaconvert list-jobs --region us-east-1 --profile default

# Check S3 bucket usage
aws s3 ls s3://mediacms-ACCOUNT-us-east-1-prod/ --recursive --human-readable --summarize --profile default
```

---

### Maintenance Tasks

**Daily:**
- Check `make -f Makefile.production task6-monitor` for anomalies
- Verify disk space: `df -h`
- Check Docker logs for errors

**Weekly:**
- Review MediaConvert job success rate
- Check S3 storage costs
- Review user-reported issues

**Monthly:**
- Database backup: `pg_dump -Fc -h localhost -U mediacms mediacms > backup-$(date +%Y%m%d).dump`
- Review and cleanup old Docker images: `docker image prune -a`
- Check for MediaCMS updates

---

### Scaling Considerations

**When to scale UP:**
- Memory usage consistently > 3 GB
- Celery queue depth consistently > 100
- API response time > 2 seconds
- MediaConvert queue delays > 1 hour

**Scaling options:**
1. Increase host memory (add RAM)
2. Increase worker concurrency (if memory allows): `CELERY_WORKER_CONCURRENCY=2`
3. Add dedicated worker host
4. Use MediaCMS `full` image with Whisper (if needed)

---

### Troubleshooting

**Service won't start:**
```bash
docker compose -f docker-compose.production.yaml --project-name mediacms-prod logs <service-name>
```

**Database connection errors:**
- Check PostgreSQL is healthy: `docker ps`
- Check credentials in `.env.production`
- Check POSTGRES_HOST=db (not localhost)

**S3 access denied:**
- Verify AWS credentials in `.env.production`
- Check IAM role permissions in CloudFormation stack
- Verify bucket name matches stack output

**MediaConvert jobs fail:**
- Check role ARN matches stack output
- Verify template names match stack (must have `-prod-` not `-dev-`)
- Check MediaConvert endpoint is correct for us-east-1

**CloudFront 403 errors:**
- Verify CloudFront distribution ID set
- Check private key file exists and is readable
- Verify key group ID matches stack

**Memory OOM:**
- Reduce worker concurrency to 1
- Stop non-essential services (mattermost/metabase if needed)
- Consider scaling to larger host

---

## Rollback to Previous Version

If you need to rollback to a previous image:

1. Update `.env.production`:
   ```bash
   MEDIACMS_IMAGE=ghcr.io/caoyjie/mediacms:<previous-commit>
   ```

2. Pull new image:
   ```bash
   make -f Makefile.production task5-pull-images
   ```

3. Restart services:
   ```bash
   docker compose -f docker-compose.production.yaml --project-name mediacms-prod up -d
   ```

4. Monitor for issues:
   ```bash
   make -f Makefile.production task6-monitor
   ```

---

## Support and Documentation

- **Plan reference:** `docs/superpowers/plans/2026-08-03-production-image-and-deployment.md`
- **AWS infrastructure:** `infra/aws/README.md`
- **MediaCMS docs:** `docs/admins_docs.md`
- **Task 1 findings:** `/etc/mediacms/release/20260803-205435/inventory.txt`

---

## Deployment Checklist Summary

- [ ] Task 1: ✅ Inspection complete
- [ ] Task 2: ✅ Configuration created
- [ ] Task 3: Build images in GitHub Actions
- [ ] Task 3: Verify images pullable
- [ ] Task 4: Validate CloudFormation templates
- [ ] Task 4: Review and execute change set
- [ ] Task 4: Extract AWS credentials
- [ ] Task 5: Create `.env.production` with secrets
- [ ] Task 5: Pull production images
- [ ] Task 5: Start infrastructure (postgres, redis)
- [ ] Task 5: Run migrations
- [ ] Task 5: Start application services
- [ ] Task 5: Complete smoke tests
- [ ] Task 6: Configure reverse proxy
- [ ] Task 6: Switch traffic
- [ ] Task 6: Monitor for 1+ hour
- [ ] Task 6: Close release report

**Total estimated time:** 2-4 hours (plus AWS propagation time)

---

**End of Guide**


