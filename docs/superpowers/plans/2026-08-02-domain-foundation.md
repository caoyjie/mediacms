# AWS Domain Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the database-enforced MediaCMS AWS domain foundation: separated media/job/cleanup states, versioned assets, resumable attempts and checkpoints, a PostgreSQL FIFO processing lease, and exactly one effective site administrator.

**Architecture:** Extend the existing `files.Media` model only with compatibility and pointer fields, while placing AWS ingestion and asset concerns in focused `files.models` modules. Keep PostgreSQL authoritative through transactions and row locks; Redis/Celery integration comes later. Bind the existing Django user model to a singleton administrator record and enforce it at reusable authentication boundaries without removing migration-dependent apps.

**Tech Stack:** Django models and migrations, PostgreSQL transactions/constraints, Django REST Framework permissions, django-allauth adapter, pytest/pytest-django, factory_boy.

## Global Constraints

- Work on `feat/aws-backend-integration`; do not import old users, media, jobs, or assets.
- Preserve `Media.state=private|public|unlisted`; processing uses `draft|queued|processing|ready|failed`.
- Preserve legacy `encoding_status` as a projection only: `draft|queued -> pending`, `processing -> running`, `ready -> success`, `failed -> fail`.
- `MediaIngestionJob.status=queued|running|failed|canceled|completed`; cleanup is independently `pending|running|failed|completed`.
- PostgreSQL is authoritative; Redis must not be required by any model or service in this plan.
- A ready media item must remain ready when cleanup fails or a replacement attempt fails.
- Only the singleton `SiteAdministrator.user` may pass the new administrator permission boundary.
- Keep RBAC, LTI, SAML, identity-provider and other migration-dependent apps installed.
- Do not connect AWS, create S3 objects, submit MediaConvert jobs, or modify Cloudflare in this plan.
- New user-facing error codes and messages are English.

---

## File Structure

- `files/models/domain.py`: shared `TextChoices` enums and the processing-to-encoding projection.
- `files/models/assets.py`: `MediaAssetVersion` and `MediaAsset` persistence only.
- `files/models/ingestion.py`: Job, Attempt, Checkpoint and singleton processing lease persistence.
- `files/services/media_state.py`: transactional processing, metadata revision, deletion and active-version transitions.
- `files/services/processing_queue.py`: FIFO enqueue/acquire/heartbeat/release operations.
- `users/models.py`: singleton `SiteAdministrator` binding.
- `users/permissions.py`: reusable DRF singleton-administrator permission.
- `users/middleware.py`: authenticated-session maintenance guard for an invalid singleton.
- `users/management/commands/init_site_administrator.py`: idempotent first-deployment administrator creation/binding.
- `files/migrations/0021_aws_domain_foundation.py` and `users/migrations/0004_siteadministrator.py`: generated schema changes with reviewed dependencies.
- `tests/aws_domain/`: focused model/service/queue tests.
- `tests/users/test_site_administrator.py`: singleton, initialization and permission tests.

### Task 1: Processing Status Compatibility on Media

**Files:**
- Create: `files/models/domain.py`
- Modify: `files/models/media.py`
- Modify: `files/models/__init__.py`
- Create: `tests/aws_domain/__init__.py`
- Create: `tests/aws_domain/test_media_processing_state.py`
- Create: `files/migrations/0021_aws_domain_foundation.py` initially through `makemigrations`

**Interfaces:**
- Produces: `MediaProcessingStatus`, `DeletionStatus`, `encoding_status_for(processing_status: str) -> str`.
- Produces: `Media.processing_status`, `Media.storage_backend`, `Media.revision`, queryable PostgreSQL `Media.metadata_sources`, `Media.deletion_status`, and nullable `Media.active_asset_version` added after Task 2 defines its target.

- [x] **Step 1: Write failing projection and default tests**

```python
import pytest

from files.models import Media
from files.models.domain import MediaProcessingStatus, encoding_status_for
from tests.users.factories import UserFactory


@pytest.mark.django_db
def test_aws_media_defaults_to_draft_without_reusing_visibility_state():
    media = Media.objects.create(title="AWS draft", user=UserFactory(), storage_backend="aws")
    assert media.state == "private"
    assert media.processing_status == MediaProcessingStatus.DRAFT
    assert media.encoding_status == "pending"
    assert media.revision == 1


@pytest.mark.parametrize(
    ("processing", "encoding"),
    [("draft", "pending"), ("queued", "pending"), ("processing", "running"), ("ready", "success"), ("failed", "fail")],
)
def test_encoding_projection(processing, encoding):
    assert encoding_status_for(processing) == encoding
```

- [x] **Step 2: Run the focused tests and verify the missing module/fields fail**

Run: `pytest tests/aws_domain/test_media_processing_state.py -q`

Expected: FAIL because `files.models.domain` and the new `Media` fields do not exist.

- [x] **Step 3: Add enums, projection, and Media scalar fields**

```python
from django.db import models


class MediaProcessingStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    QUEUED = "queued", "Queued"
    PROCESSING = "processing", "Processing"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class DeletionStatus(models.TextChoices):
    NONE = "none", "None"
    PENDING = "pending", "Pending"
    DELETING = "deleting", "Deleting"
    FAILED = "failed", "Failed"
    COMPLETED = "completed", "Completed"


ENCODING_STATUS_BY_PROCESSING_STATUS = {
    MediaProcessingStatus.DRAFT: "pending",
    MediaProcessingStatus.QUEUED: "pending",
    MediaProcessingStatus.PROCESSING: "running",
    MediaProcessingStatus.READY: "success",
    MediaProcessingStatus.FAILED: "fail",
}


def encoding_status_for(processing_status: str) -> str:
    return ENCODING_STATUS_BY_PROCESSING_STATUS[processing_status]
```

Add indexed `processing_status`, indexed `storage_backend` with `legacy_local|aws`, positive `revision=1`, `metadata_sources=JSONField(default=dict, blank=True)`, and indexed `deletion_status=none` to `Media`. PostgreSQL JSON key queries make field provenance queryable without adding one row per metadata field. Values are restricted by the Task 4 service to `admin|file_probe|youtube|default`. Do not override `Media.save()` to project status implicitly; state transitions must use the service in Task 4.

- [x] **Step 4: Generate the migration and verify it contains only intended scalar fields**

Run: `python manage.py makemigrations files --name aws_domain_foundation`

Expected: one new `files` migration adding `processing_status`, `storage_backend`, `revision`, `metadata_sources`, and `deletion_status`; no unrelated alterations.

- [x] **Step 5: Run focused tests**

Run: `pytest tests/aws_domain/test_media_processing_state.py -q`

Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add files/models/domain.py files/models/media.py files/models/__init__.py files/migrations/0021_aws_domain_foundation.py tests/aws_domain
git commit -m "feat: add media processing state foundation"
```

### Task 2: Versioned Media Assets and Atomic Pointer Schema

**Files:**
- Create: `files/models/assets.py`
- Modify: `files/models/media.py`
- Modify: `files/models/__init__.py`
- Create: `files/migrations/0022_media_asset_version.py`; `0021` is already committed and must not be rewritten
- Create: `tests/aws_domain/test_media_assets.py`

**Interfaces:**
- Consumes: `MediaProcessingStatus` from Task 1.
- Produces: `MediaAssetVersion.Status`, `MediaAsset.Kind`, `Media.active_asset_version` and exact `(version, s3_key)` uniqueness.

- [x] **Step 1: Write failing version integrity tests**

```python
import pytest
from django.db import IntegrityError

from files.models import Media, MediaAsset, MediaAssetVersion
from tests.users.factories import UserFactory


@pytest.mark.django_db
def test_asset_key_is_unique_inside_a_version():
    media = Media.objects.create(title="Asset test", user=UserFactory(), storage_backend="aws")
    version = MediaAssetVersion.objects.create(media=media, status="candidate", manifest_key="media/1/candidate/master.m3u8")
    MediaAsset.objects.create(version=version, kind="hls_master", s3_key=version.manifest_key, checksum="sha256:one")
    with pytest.raises(IntegrityError):
        MediaAsset.objects.create(version=version, kind="hls_master", s3_key=version.manifest_key, checksum="sha256:two")


@pytest.mark.django_db
def test_active_pointer_references_a_complete_version():
    media = Media.objects.create(title="Pointer test", user=UserFactory(), storage_backend="aws")
    version = MediaAssetVersion.objects.create(media=media, status="candidate", manifest_key="media/2/candidate/master.m3u8")
    media.active_asset_version = version
    media.save(update_fields=["active_asset_version"])
    assert media.active_asset_version_id == version.id
```

- [x] **Step 2: Run tests and verify missing models fail**

Run: `pytest tests/aws_domain/test_media_assets.py -q`

Expected: FAIL importing `MediaAsset` and `MediaAssetVersion`.

- [x] **Step 3: Implement focused asset models**

Use UUID primary keys. `MediaAssetVersion` initially has `media=ForeignKey(PROTECT, related_name="asset_versions")`, `status=candidate|active|retired`, `manifest_key`, `activated_at`, `created_at`, and `updated_at`. Task 3 adds the Attempt link after `MediaJobAttempt` exists. `MediaAsset` has `version=ForeignKey(CASCADE, related_name="assets")`, `kind=hls_master|hls_variant|hls_segment|poster|thumbnail|subtitle|audio`, `s3_key`, `checksum`, `size_bytes`, and `content_type`. Add `UniqueConstraint(fields=("version", "s3_key"), name="files_asset_key_per_version_uniq")`.

Add nullable `Media.active_asset_version=ForeignKey("MediaAssetVersion", SET_NULL, related_name="active_for_media")`. Model-level circular ownership is allowed, but Task 4 must validate that the selected version belongs to the locked Media before activation.

- [x] **Step 4: Generate and inspect the asset migration**

Run: `python manage.py makemigrations files --name media_asset_version`

Expected: `0022_media_asset_version.py` creates both asset models and the Media pointer, with no data migration or unrelated alterations.

- [x] **Step 5: Run asset and processing tests**

Run: `pytest tests/aws_domain/test_media_assets.py tests/aws_domain/test_media_processing_state.py -q`

Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add files/models/assets.py files/models/media.py files/models/__init__.py files/migrations/0022_media_asset_version.py tests/aws_domain/test_media_assets.py
git commit -m "feat: add versioned media assets"
```

### Task 3: Jobs, Attempts, Checkpoints, and Processing Lease

**Files:**
- Create: `files/models/ingestion.py`
- Modify: `files/models/__init__.py`
- Create: `files/migrations/0023_ingestion_models.py`; committed migrations `0021` and `0022` must not be rewritten
- Create: `tests/aws_domain/test_ingestion_models.py`

**Interfaces:**
- Produces: `MediaIngestionJob`, `MediaJobAttempt`, `MediaJobCheckpoint`, `ProcessingLease` and their `TextChoices`.
- Produces: chronological `MediaIngestionJob.objects.queued()` ordering by `(queued_at, id)`.

- [x] **Step 1: Write failing constraint and ordering tests**

```python
import pytest
from django.db import IntegrityError
from django.utils import timezone

from files.models import Media, MediaIngestionJob, MediaJobAttempt, ProcessingLease
from tests.users.factories import UserFactory


@pytest.mark.django_db
def test_attempt_sequence_is_unique_per_job():
    media = Media.objects.create(title="Job test", user=UserFactory(), storage_backend="aws")
    job = MediaIngestionJob.objects.create(media=media, source_type="upload", queued_at=timezone.now())
    MediaJobAttempt.objects.create(job=job, sequence=1, status="queued")
    with pytest.raises(IntegrityError):
        MediaJobAttempt.objects.create(job=job, sequence=1, status="queued")


@pytest.mark.django_db
def test_processing_lease_is_a_singleton_row():
    ProcessingLease.objects.create(singleton_key="default")
    with pytest.raises(IntegrityError):
        ProcessingLease.objects.create(singleton_key="default")
```

- [x] **Step 2: Run tests and verify model imports fail**

Run: `pytest tests/aws_domain/test_ingestion_models.py -q`

Expected: FAIL importing the new ingestion models.

- [x] **Step 3: Implement persistence models and explicit indexes**

Use UUID primary keys for Job and Attempt. Job fields: nullable Media FK with `SET_NULL` plus `media_title_snapshot`, `source_type=upload|hls_zip|youtube`, `status`, `stage`, decimal `progress` constrained to `0..100`, `cancel_requested`, independent `cleanup_status`, JSON `source_metadata`, `safe_error`, `queued_at`, timestamps, and an index on `(status, queued_at, id)`. `SET_NULL` is required so audit history survives eventual Media deletion. Attempt fields match the approved spec and include unique `(job, sequence)`. Diagnostic errors never appear in `__str__`. Add nullable `MediaAssetVersion.attempt=OneToOneField(MediaJobAttempt, SET_NULL, related_name="asset_version")` now that both model classes exist.

Checkpoint fields: Attempt FK, `name`, `status=pending|completed|available|unavailable|failed_retryable`, `input_fingerprint`, JSON `evidence`, `completed_at`, timestamps, and unique `(attempt, name)`. ProcessingLease uses primary-key `singleton_key="default"`, nullable protected Job/Attempt FKs, `owner_token`, `heartbeat_at`, and `expires_at`.

- [x] **Step 4: Generate and inspect the files migration**

Run: `python manage.py makemigrations files --name ingestion_models`

Expected: one coherent migration with enums materialized as field choices, named constraints/indexes, and no AWS calls.

- [x] **Step 5: Run all domain model tests**

Run: `pytest tests/aws_domain/test_ingestion_models.py tests/aws_domain/test_media_assets.py tests/aws_domain/test_media_processing_state.py -q`

Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add files/models/ingestion.py files/models/assets.py files/models/__init__.py files/migrations/0023_ingestion_models.py tests/aws_domain/test_ingestion_models.py
git commit -m "feat: add ingestion persistence models"
```

### Task 4: Transactional Media State, Metadata Revision, Deletion, and Asset Activation

**Files:**
- Create: `files/services/__init__.py`
- Create: `files/services/media_state.py`
- Create: `tests/aws_domain/test_media_state_service.py`

**Interfaces:**
- Consumes: `Media`, `MediaAssetVersion`, `MediaProcessingStatus`, `encoding_status_for`.
- Produces: `transition_media(media_id: int, target: str) -> Media`, `update_media_metadata(media_id: int, expected_revision: int, changes: dict, source: str) -> Media`, `request_media_deletion(media_id: int, expected_revision: int) -> Media`, and `activate_asset_version(media_id: int, version_id: UUID) -> Media`.

- [ ] **Step 1: Write failing transition and replacement-safety tests**

```python
import pytest

from files.models import Media, MediaAsset, MediaAssetVersion
from files.services.media_state import activate_asset_version, transition_media, update_media_metadata
from tests.users.factories import UserFactory


@pytest.mark.django_db(transaction=True)
def test_transition_projects_legacy_encoding_status():
    media = Media.objects.create(title="Transition", user=UserFactory(), storage_backend="aws")
    transition_media(media.id, "queued")
    media.refresh_from_db()
    assert (media.processing_status, media.encoding_status) == ("queued", "pending")


@pytest.mark.django_db(transaction=True)
def test_activation_switches_complete_version_in_one_transaction():
    media = Media.objects.create(title="Activation", user=UserFactory(), storage_backend="aws")
    candidate = MediaAssetVersion.objects.create(media=media, status="candidate", manifest_key="media/a/master.m3u8")
    MediaAsset.objects.create(version=candidate, kind="hls_master", s3_key=candidate.manifest_key, checksum="sha256:manifest")
    activate_asset_version(media.id, candidate.id)
    media.refresh_from_db()
    candidate.refresh_from_db()
    assert media.active_asset_version_id == candidate.id
    assert media.processing_status == "ready"
    assert media.encoding_status == "success"
    assert candidate.status == "active"


@pytest.mark.django_db(transaction=True)
def test_admin_metadata_update_increments_revision_and_owns_field_source():
    media = Media.objects.create(title="Before", user=UserFactory(), storage_backend="aws")
    updated = update_media_metadata(media.id, expected_revision=1, changes={"title": "After"}, source="admin")
    assert updated.revision == 2
    assert updated.metadata_sources["title"] == "admin"
```

- [ ] **Step 2: Run tests and verify the service is missing**

Run: `pytest tests/aws_domain/test_media_state_service.py -q`

Expected: FAIL importing `files.services.media_state`.

- [ ] **Step 3: Implement explicit transition graph and locked activation**

Define transitions `draft->queued`, `queued->processing`, `processing->ready|failed`, and `failed->queued`. Lock Media with `select_for_update()`. Activation locks Media plus target version, rejects cross-media versions and non-candidates, verifies that the candidate contains a registered `hls_master` asset matching `manifest_key`, retires the previous active version, activates the candidate, switches the pointer, and projects `ready/success` inside one `transaction.atomic()` block. Do not delete S3/local objects in this service.

`update_media_metadata` accepts only `title`, `description`, `state`, `category_ids`, and `tag_ids` in this phase. It compares category/tag IDs as sets, checks `expected_revision`, raises `MediaRevisionConflict(current_revision, current_values)` on mismatch, updates only changed fields, assigns the supplied provenance, and increments revision once. Automatic sources may fill empty fields or replace fields still owned by the same automatic source; they may never overwrite a field owned by `admin`.

`request_media_deletion` checks revision, changes `deletion_status` from `none|failed` to `pending`, increments revision once, sets `listable=False`, and does not delete Media, Job, Attempt or objects. Later orchestration owns cancel and cleanup execution.

- [ ] **Step 4: Add negative tests**

Cover illegal `draft->ready`, candidate belonging to another Media, candidate without its registered manifest asset, activating retired version, replacement failure leaving the old pointer unchanged, cleanup failure without changing ready Media, stale metadata revision conflict, category/tag order not incrementing revision, automatic source not overwriting admin data, repeated deletion request idempotency, and Job history surviving a nullable Media deletion.

Run: `pytest tests/aws_domain/test_media_state_service.py -q`

Expected: PASS with all positive and negative cases.

- [ ] **Step 5: Commit**

```bash
git add files/services tests/aws_domain/test_media_state_service.py
git commit -m "feat: enforce media state transitions"
```

### Task 5: PostgreSQL FIFO Processing Lease

**Files:**
- Create: `files/services/processing_queue.py`
- Create: `tests/aws_domain/test_processing_queue.py`

**Interfaces:**
- Consumes: `MediaIngestionJob`, `MediaJobAttempt`, `ProcessingLease`.
- Produces: `enqueue_job(job_id: UUID)`, `acquire_head_job(owner_token: str, lease_seconds: int, now=None) -> LeaseAcquisition | None`, `heartbeat_lease(owner_token: str, lease_seconds: int, now=None)`, and `release_lease(owner_token: str)`.

- [ ] **Step 1: Write failing FIFO and lease exclusion tests**

```python
import pytest

from files.services.processing_queue import acquire_head_job


@pytest.mark.django_db(transaction=True)
def test_only_oldest_queued_job_is_acquired(two_queued_jobs):
    acquired = acquire_head_job("worker-a", lease_seconds=60)
    assert acquired.job_id == two_queued_jobs[0].id


@pytest.mark.django_db(transaction=True)
def test_live_lease_blocks_second_owner(two_queued_jobs):
    assert acquire_head_job("worker-a", lease_seconds=60) is not None
    assert acquire_head_job("worker-b", lease_seconds=60) is None
```

- [ ] **Step 2: Run tests and verify the queue service is missing**

Run: `pytest tests/aws_domain/test_processing_queue.py -q`

Expected: FAIL importing `processing_queue`.

- [ ] **Step 3: Implement locked singleton acquisition**

Inside `transaction.atomic()`, ensure the `default` ProcessingLease row exists, lock it with `select_for_update()`, reject a non-expired different owner, then lock the first queued Job ordered by `queued_at,id`. Create the next Attempt sequence under the same transaction when the Job has no resumable queued Attempt, set Job/Attempt running, bind the lease, and return an immutable `LeaseAcquisition(job_id, attempt_id, expires_at)` dataclass. Heartbeat and release require exact owner token; release clears ownership but preserves the singleton row.

- [ ] **Step 4: Add expiry and crash-recovery tests**

Test expired takeover, wrong-owner heartbeat/release rejection, no queued job, stable tie-breaking by UUID, Attempt sequence increment on Resume, and two database connections racing for acquisition with exactly one winner. Skip the true concurrency case only when the test database vendor is not PostgreSQL.

Run: `pytest tests/aws_domain/test_processing_queue.py -q`

Expected: PASS; PostgreSQL run proves exactly one acquisition.

- [ ] **Step 5: Commit**

```bash
git add files/services/processing_queue.py tests/aws_domain/test_processing_queue.py
git commit -m "feat: add postgres fifo processing lease"
```

### Task 6: Singleton Site Administrator and Idempotent Initialization

**Files:**
- Modify: `users/models.py`
- Modify: `users/admin.py`
- Create: `users/management/__init__.py`
- Create: `users/management/commands/__init__.py`
- Create: `users/management/commands/init_site_administrator.py`
- Create: `users/migrations/0004_siteadministrator.py`
- Create: `tests/users/test_site_administrator.py`

**Interfaces:**
- Produces: `SiteAdministrator.get_solo()`, `SiteAdministrator.is_site_administrator(user) -> bool`.
- Produces command: `python manage.py init_site_administrator --username NAME --email ADDRESS`, with password accepted only through `MEDIACMS_ADMIN_PASSWORD` or interactive hidden prompt; non-interactive mode is `--no-input`.

- [ ] **Step 1: Write failing singleton and idempotency tests**

```python
import pytest
from django.core.management import call_command
from django.db import IntegrityError

from users.models import SiteAdministrator, User


@pytest.mark.django_db
def test_only_default_singleton_key_is_allowed():
    user = User.objects.create_user(username="admin", email="admin@example.invalid", password="strong-test-password")
    SiteAdministrator.objects.create(user=user)
    with pytest.raises(IntegrityError):
        SiteAdministrator.objects.create(singleton_key="other", user=User.objects.create_user(username="other"))


@pytest.mark.django_db
def test_init_command_is_idempotent(monkeypatch):
    monkeypatch.setenv("MEDIACMS_ADMIN_PASSWORD", "strong-test-password")
    call_command("init_site_administrator", username="admin", email="admin@example.invalid", interactive=False)
    call_command("init_site_administrator", username="admin", email="admin@example.invalid", interactive=False)
    assert User.objects.count() == 1
    assert SiteAdministrator.objects.count() == 1
```

- [ ] **Step 2: Run tests and verify the model/command are missing**

Run: `pytest tests/users/test_site_administrator.py -q`

Expected: FAIL importing `SiteAdministrator`.

- [ ] **Step 3: Implement singleton schema and manager methods**

Use `singleton_key=CharField(primary_key=True, default="default", editable=False)`, `user=OneToOneField(User, PROTECT, related_name="site_administrator_binding")`, timestamps, and `CheckConstraint(condition=Q(singleton_key="default"), name="users_site_admin_default_key")`. `is_site_administrator` must require authenticated, active, approved-compatible user identity and the exact singleton binding.

- [ ] **Step 4: Implement safe idempotent initialization**

The command runs in `transaction.atomic()`, locks the singleton lookup, creates or updates the named user as active/staff/superuser/approved, binds it, and deactivates every other user. It never prints the password and rejects non-interactive execution without `MEDIACMS_ADMIN_PASSWORD`. Re-running with the same username does not create rows; binding a different username requires explicit `--rebind`.

- [ ] **Step 5: Remove User add/delete capabilities from Django Admin**

Keep the User model registered for dependency visibility but make `UserAdmin.has_add_permission()` and `has_delete_permission()` return `False`; restrict queryset/change permission to the bound administrator. Register `SiteAdministrator` read-only with add/delete disabled.

- [ ] **Step 6: Generate migration and run tests**

Run: `python manage.py makemigrations users --name siteadministrator`

Expected: `users/migrations/0004_siteadministrator.py` with the singleton check and OneToOne constraint.

Run: `pytest tests/users/test_site_administrator.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add users/models.py users/admin.py users/management users/migrations/0004_siteadministrator.py tests/users/test_site_administrator.py
git commit -m "feat: enforce singleton site administrator"
```

### Task 7: Administrator Authentication Boundary and Disabled Signup

**Files:**
- Create: `users/permissions.py`
- Create: `users/middleware.py`
- Modify: `users/adapter.py`
- Modify: `cms/settings.py`
- Create: `cms/aws_settings.py`
- Modify: `cms/urls.py`
- Modify: `templates/account/login.html`
- Create: `tests/users/test_single_admin_access.py`

**Interfaces:**
- Consumes: `SiteAdministrator.is_site_administrator`.
- Produces: `IsSiteAdministrator` DRF permission and `SiteAdministratorGuardMiddleware`.
- Produces: maintenance response code `site_administrator_unavailable` for authenticated protected requests when the singleton is absent/invalid.

- [ ] **Step 1: Write failing access-boundary tests**

```python
import pytest
from django.test import Client, override_settings


@pytest.mark.django_db
@override_settings(MEDIACMS_SINGLE_ADMIN_MODE=True)
def test_signup_is_closed(site_administrator):
    assert Client().get("/accounts/signup/").status_code in {403, 404}


@pytest.mark.django_db
@override_settings(MEDIACMS_SINGLE_ADMIN_MODE=True)
def test_non_bound_active_user_cannot_use_protected_api(site_administrator, other_user):
    client = Client()
    client.force_login(other_user)
    response = client.get("/api/v1/whoami")
    assert response.status_code == 403
```

- [ ] **Step 2: Run tests and verify current multi-user behavior fails them**

Run: `pytest tests/users/test_single_admin_access.py -q`

Expected: FAIL because signup/current authenticated APIs still accept ordinary users.

- [ ] **Step 3: Implement settings-driven single-admin boundary**

Set `MEDIACMS_SINGLE_ADMIN_MODE=False` as the compatibility default in `cms/settings.py`. Create `cms/aws_settings.py` importing base settings and overriding `MEDIACMS_SINGLE_ADMIN_MODE=True`, `USERS_CAN_SELF_REGISTER=False`, and `REGISTER_ALLOWED=False`; AWS Compose and all new AWS tests use `DJANGO_SETTINGS_MODULE=cms.aws_settings`. Make `MyAccountAdapter.is_open_for_signup()` return false in single-admin mode. Keep allauth login/password management routes needed by the administrator, but map `/accounts/signup/` to an English 404/disabled response before including allauth URLs. Remove signup copy from the login template.

`IsSiteAdministrator.has_permission()` delegates to the singleton model. The middleware checks authenticated requests in single-admin mode, exempts static/media, login/logout, health, and the administrator repair command (which is not HTTP), returns JSON `503 {"code":"site_administrator_unavailable"}` when the binding is invalid, and returns `403 {"code":"single_administrator_required"}` for a different authenticated user on API paths. HTML paths log the other user out and redirect to login without a redirect loop.

- [ ] **Step 4: Wire the boundary without removing installed apps**

Add middleware after Django `AuthenticationMiddleware`. Apply `IsSiteAdministrator` first to the existing authenticated management/API base points used by the AWS implementation; do not change public media read endpoints in this task. Assert settings still contain RBAC, LTI, SAML, identity providers and actions.

- [ ] **Step 5: Run focused and existing authentication regressions**

Run: `pytest tests/users/test_single_admin_access.py tests/users/test_session_version.py tests/api/test_user_login.py tests/api/test_user_whoami.py -q`

Expected: PASS. Existing base-settings regression tests retain compatibility mode; `test_single_admin_access.py` explicitly uses AWS settings/overrides and its fixture creates `SiteAdministrator(user=login_user)`.

- [ ] **Step 6: Commit**

```bash
git add users/permissions.py users/middleware.py users/adapter.py cms/settings.py cms/aws_settings.py cms/urls.py templates/account/login.html tests/users/test_single_admin_access.py
git commit -m "feat: enforce single administrator access"
```

### Task 8: Legacy Pipeline Guard and Empty-Database Verification

**Files:**
- Create: `files/services/storage_backend.py`
- Modify: `files/models/media.py`
- Modify: `files/models/media.py` scheduling sites in `save()`, `media_init()`, `set_encoding_status()` and `media_post_save()`
- Modify: `files/tasks.py` entry points `encode_media`, `create_hls`, `media_init`, `post_trim_action`, and `video_trim_task`
- Modify: `files/methods.py` entry point `create_video_trim_request`
- Modify: `files/views/pages.py` endpoint `trim_video`
- Create: `tests/aws_domain/test_legacy_pipeline_guard.py`
- Create: `tests/aws_domain/test_empty_database_bootstrap.py`
- Modify: `docs/superpowers/plans/2026-08-02-aws-integration-roadmap.md` to mark only Domain foundation complete after every gate passes

**Interfaces:**
- Produces: `uses_aws_pipeline(media: Media) -> bool` and `legacy_processing_allowed(media: Media) -> bool`.
- Guarantees: saving AWS Media cannot enqueue local encoding, HLS, sprite, trim or local playback fallback tasks.

- [ ] **Step 1: Confirm the approved legacy entry-point inventory has not drifted**

Run: `rg -n "post_save|media_init|create_hls|encode_media|sprites|trim" files uploader`

Expected: the command still identifies the explicit scheduling and execution boundaries listed in this task's Files section. If it finds a new executable boundary, add its exact function and file to this plan before implementation; migrations, field declarations and URL strings are not executable boundaries.

- [ ] **Step 2: Write a failing spy-based regression test**

Create an AWS Media with `storage_backend="aws"`; patch `files.models.media.tasks.media_init.apply_async`, `files.models.media.tasks.encode_media.apply_async`, `files.models.media.tasks.create_hls.delay`, `files.models.media.tasks.post_trim_action.delay`, and `files.views.pages.video_trim_task.delay`; save and initialize the media, then assert every spy has zero calls. Directly call each guarded task with an AWS media identifier and assert it exits before accessing a local path. Add a legacy-local control test proving the guard does not silently delete existing behavior outside AWS mode.

Run: `pytest tests/aws_domain/test_legacy_pipeline_guard.py -q`

Expected: FAIL by observing at least one current local processing entry point.

- [ ] **Step 3: Add one central backend predicate and guard every discovered entry point**

Use `legacy_processing_allowed(media)` at signal and direct task scheduling boundaries. Do not rely on `DO_NOT_TRANSCODE_VIDEO` alone. AWS mode rejects a legacy-local import request at its service/API boundary but does not remove legacy models, migrations, tables or ContentTypes.

- [ ] **Step 4: Verify the guard and preserved app graph**

Run: `pytest tests/aws_domain/test_legacy_pipeline_guard.py tests/test_imports.py tests/settings/test_portal_workflow.py -q`

Expected: PASS; installed migration-dependent apps remain importable.

- [ ] **Step 5: Verify migrations from an empty PostgreSQL database**

Run inside the development Compose PostgreSQL environment with a newly named empty test database:

```bash
python manage.py migrate --noinput
python manage.py init_site_administrator --username admin --email admin@example.invalid --no-input
python manage.py migrate --plan
python manage.py check --deploy
```

Expected: all migrations apply once, the initialization command creates exactly one active user/binding, the second command run is idempotent, migration plan is empty, and deploy check has no new AWS-domain errors. Supply `MEDIACMS_ADMIN_PASSWORD` through the process environment without printing it.

- [ ] **Step 6: Run the complete Phase 1 verification suite**

Run:

```bash
pytest tests/aws_domain tests/users/test_site_administrator.py tests/users/test_single_admin_access.py tests/users/test_session_version.py tests/api/test_user_login.py tests/api/test_user_whoami.py tests/test_imports.py tests/settings/test_portal_workflow.py -q
python manage.py makemigrations --check --dry-run
python manage.py check
git diff --check
```

Expected: zero test failures, no missing migrations, no Django check errors, and no whitespace errors.

- [ ] **Step 7: Update status only with evidence and commit**

Mark only the Domain foundation row in the implementation roadmap complete after Step 6 and the empty-PostgreSQL migration proof pass. The overall design remains in implementation until all plans finish. Record test command outputs in the commit/PR description, not in generated environment reports.

```bash
git add files tests users cms templates docs/superpowers/plans/2026-08-02-aws-integration-roadmap.md
git commit -m "feat: complete aws domain foundation"
```

## Plan Completion Gate

The plan is complete only when:

- Empty PostgreSQL migration and idempotent singleton initialization pass.
- Database constraints reject duplicate Attempt sequences and non-default administrator singleton keys.
- A concurrent PostgreSQL lease test proves only the FIFO head is acquired.
- Atomic activation cannot expose a cross-media, retired, or incomplete candidate.
- Cleanup failure and replacement failure preserve the existing ready active version.
- Stale metadata writes produce a deterministic revision conflict; automatic metadata never overwrites administrator-owned values.
- A deletion request hides media and preserves Job/Attempt audit history without deleting objects synchronously.
- A non-bound active user cannot log in to protected management/API surfaces.
- Signup and multi-user write entry points are disabled without removing migration-dependent apps.
- Spy tests prove AWS Media never schedules legacy local encoding/HLS/sprite/trim work.
- The focused regression suite, Django checks, migration check and `git diff --check` all pass.
