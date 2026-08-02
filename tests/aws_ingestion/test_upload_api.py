from dataclasses import asdict
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from files.models import BrowserUploadSession
from files.services.s3_uploads import S3ObjectEvidence, S3Part
from tests.aws_ingestion.test_hls_upload_sessions import CHECKSUM, HlsGateway, hls_entries
from tests.aws_ingestion.test_upload_sessions import (
    RecordingPromotionStorage,
    RecordingUploadGateway,
)
from users.models import SiteAdministrator, User


@pytest.fixture
def administrator(db):
    user = User.objects.create_user(
        username="upload-admin",
        password="test-password",
        is_active=True,
        is_staff=True,
        is_superuser=True,
        is_approved=True,
    )
    SiteAdministrator.objects.create(user=user)
    return user


@pytest.fixture
def ordinary_user(db):
    return User.objects.create_user(username="ordinary", is_active=True, is_approved=True)


@pytest.fixture
def gateway():
    return RecordingUploadGateway()


@pytest.fixture
def promotion_storage():
    return RecordingPromotionStorage()


def authenticated_client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.mark.django_db
def test_upload_api_rejects_anonymous_and_non_bound_users(administrator, ordinary_user):
    url = "/api/v1/aws/uploads/"
    assert APIClient().get(url).status_code in {401, 403}
    assert authenticated_client(ordinary_user).get(url).status_code == 403


@pytest.mark.django_db
def test_file_session_create_is_json_only_strict_and_does_not_expose_signed_url(administrator, gateway):
    client = authenticated_client(administrator)
    payload = {
        "source_kind": "file",
        "title": "Short video",
        "media_type": "video",
        "filename": "short.mp4",
        "size": 100,
        "content_type": "video/mp4",
        "fingerprint": "file-fingerprint",
    }
    with patch("files.views.aws_uploads._gateway", return_value=gateway):
        unsupported = client.post(
            "/api/v1/aws/uploads/",
            data="source_kind=file",
            content_type="application/x-www-form-urlencoded",
            HTTP_IDEMPOTENCY_KEY="create-1",
        )
        unknown = client.post(
            "/api/v1/aws/uploads/",
            {**payload, "unexpected": True},
            format="json",
            HTTP_IDEMPOTENCY_KEY="create-2",
        )
        response = client.post(
            "/api/v1/aws/uploads/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="create-1",
        )

    assert unsupported.status_code == 415
    assert unknown.status_code == 400
    assert unknown.json()["code"] == "invalid_request"
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "waiting"
    assert body["revision"] == 1
    assert "url" not in str(body).lower()
    assert BrowserUploadSession.objects.count() == 1


@pytest.mark.django_db
def test_detail_and_lease_actions_project_safe_progress(administrator, gateway):
    client = authenticated_client(administrator)
    payload = {
        "source_kind": "file",
        "title": "Short video",
        "media_type": "video",
        "filename": "short.mp4",
        "size": 100,
        "content_type": "video/mp4",
        "fingerprint": "file-fingerprint",
    }
    with patch("files.views.aws_uploads._gateway", return_value=gateway):
        created = client.post(
            "/api/v1/aws/uploads/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="create-1",
        ).json()
        session_id = created["id"]
        acquired = client.post(
            f"/api/v1/aws/uploads/{session_id}/lease/acquire/",
            {"lease_seconds": 60},
            format="json",
            HTTP_IDEMPOTENCY_KEY="lease-1",
            HTTP_UPLOAD_LEASE_TOKEN="browser-a",
        )
        detail = client.get(f"/api/v1/aws/uploads/{session_id}/")

    assert acquired.status_code == 200
    assert acquired.json()["status"] == "uploading"
    assert acquired.json()["revision"] == 2
    assert detail.status_code == 200
    assert detail.json()["confirmed_bytes"] == 0
    assert detail.json()["total_bytes"] == 100
    assert "multipart_upload_id" not in str(detail.json())
    assert "url" not in str(detail.json()).lower()


@pytest.mark.django_db
def test_file_api_sign_reconcile_and_complete_flow(administrator, gateway, promotion_storage):
    client = authenticated_client(administrator)
    checksum = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
    payload = {
        "source_kind": "file",
        "title": "Upload flow",
        "media_type": "video",
        "filename": "flow.mp4",
        "size": 32_000_000,
        "content_type": "video/mp4",
        "fingerprint": "flow-fingerprint",
    }
    gateway.parts = (
        S3Part(1, '"etag-1"', 16_000_000, checksum),
        S3Part(2, '"etag-2"', 16_000_000, checksum),
    )
    gateway.head_evidence = S3ObjectEvidence(32_000_000, "video/mp4", '"final"', checksum)

    with (
        patch("files.views.aws_uploads._gateway", return_value=gateway),
        patch(
            "files.views.aws_uploads._processing_storage",
            return_value=promotion_storage,
        ),
    ):
        created = client.post(
            "/api/v1/aws/uploads/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="flow-create",
        ).json()
        base = f"/api/v1/aws/uploads/{created['id']}"
        client.post(
            f"{base}/lease/acquire/",
            {"lease_seconds": 60},
            format="json",
            HTTP_IDEMPOTENCY_KEY="flow-lease",
            HTTP_UPLOAD_LEASE_TOKEN="browser-flow",
        )
        signed = client.post(
            f"{base}/parts/urls/",
            {"parts": [{"part_number": 1, "checksum_sha256": checksum}]},
            format="json",
            HTTP_IDEMPOTENCY_KEY="flow-sign",
            HTTP_UPLOAD_LEASE_TOKEN="browser-flow",
        )
        reconciled = client.post(
            f"{base}/reconcile/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="flow-reconcile",
            HTTP_UPLOAD_LEASE_TOKEN="browser-flow",
        )
        completed = client.post(
            f"{base}/complete/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="flow-complete",
            HTTP_UPLOAD_LEASE_TOKEN="browser-flow",
            HTTP_IF_MATCH='"3"',
        )

    assert signed.status_code == 200
    assert signed.json()["requests"][0]["url"].startswith("https://signed.example.invalid/")
    assert reconciled.status_code == 200
    assert reconciled.json()["confirmed_bytes"] == 32_000_000
    assert reconciled.json()["revision"] == 3
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["stage"] == "source_verified"
    assert "url" not in str(completed.json()).lower()


@pytest.mark.django_db
def test_stale_completion_revision_is_precondition_failed(
    administrator,
    gateway,
    promotion_storage,
):
    client = authenticated_client(administrator)
    payload = {
        "source_kind": "file",
        "title": "Stale",
        "media_type": "audio",
        "filename": "stale.mp3",
        "size": 100,
        "content_type": "audio/mpeg",
        "fingerprint": "stale-fingerprint",
    }
    with (
        patch("files.views.aws_uploads._gateway", return_value=gateway),
        patch(
            "files.views.aws_uploads._processing_storage",
            return_value=promotion_storage,
        ),
    ):
        created = client.post(
            "/api/v1/aws/uploads/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="stale-create",
        ).json()
        base = f"/api/v1/aws/uploads/{created['id']}"
        client.post(
            f"{base}/lease/acquire/",
            {"lease_seconds": 60},
            format="json",
            HTTP_IDEMPOTENCY_KEY="stale-lease",
            HTTP_UPLOAD_LEASE_TOKEN="browser-stale",
        )
        response = client.post(
            f"{base}/complete/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="stale-complete",
            HTTP_UPLOAD_LEASE_TOKEN="browser-stale",
            HTTP_IF_MATCH='"1"',
        )

    assert response.status_code == 412
    assert response.json() == {"code": "revision_conflict", "current_revision": 2}


@pytest.mark.django_db
def test_hls_api_register_sign_and_complete_flow(administrator):
    client = authenticated_client(administrator)
    gateway = HlsGateway()
    payload = {
        "source_kind": "hls",
        "title": "HLS package",
        "total_size": 1_200,
        "file_count": 3,
        "package_fingerprint": "hls-fingerprint",
    }
    with patch("files.views.aws_uploads._gateway", return_value=gateway):
        created = client.post(
            "/api/v1/aws/uploads/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="hls-create",
        ).json()
        base = f"/api/v1/aws/uploads/{created['id']}"
        client.post(
            f"{base}/lease/acquire/",
            {"lease_seconds": 60},
            format="json",
            HTTP_IDEMPOTENCY_KEY="hls-lease",
            HTTP_UPLOAD_LEASE_TOKEN="browser-hls",
        )
        registered = client.post(
            f"{base}/objects/register/",
            {"entries": [asdict(entry) for entry in hls_entries()]},
            format="json",
            HTTP_IDEMPOTENCY_KEY="hls-register",
            HTTP_UPLOAD_LEASE_TOKEN="browser-hls",
        )
        segment = next(item for item in registered.json()["objects"] if item["path"] == "video/segment.ts")
        signed = client.post(
            f"{base}/objects/{segment['id']}/url/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="hls-sign",
            HTTP_UPLOAD_LEASE_TOKEN="browser-hls",
        )
        for upload_object in BrowserUploadSession.objects.get(pk=created["id"]).upload_objects.all():
            gateway.head_evidence[upload_object.s3_key] = S3ObjectEvidence(
                upload_object.expected_size,
                upload_object.content_type,
                '"etag"',
                CHECKSUM,
            )
        completed = client.post(
            f"{base}/complete/",
            {
                "manifest_bodies": {
                    "master.m3u8": "#EXTM3U\nvideo/playlist.m3u8\n",
                    "video/playlist.m3u8": "#EXTM3U\nsegment.ts\n#EXT-X-ENDLIST\n",
                }
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="hls-complete",
            HTTP_UPLOAD_LEASE_TOKEN="browser-hls",
            HTTP_IF_MATCH='"3"',
        )

    assert registered.status_code == 200
    assert signed.status_code == 200
    assert signed.json()["request"]["url"].startswith("https://signed.example.invalid/")
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"


@pytest.mark.django_db
def test_upload_api_heartbeat_pause_resume_and_cancel(administrator, gateway):
    client = authenticated_client(administrator)
    payload = {
        "source_kind": "file",
        "title": "Lifecycle",
        "media_type": "audio",
        "filename": "lifecycle.mp3",
        "size": 100,
        "content_type": "audio/mpeg",
        "fingerprint": "lifecycle-fingerprint",
    }
    with patch("files.views.aws_uploads._gateway", return_value=gateway):
        created = client.post(
            "/api/v1/aws/uploads/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="lifecycle-create",
        ).json()
        base = f"/api/v1/aws/uploads/{created['id']}"
        common = {
            "format": "json",
            "HTTP_UPLOAD_LEASE_TOKEN": "browser-lifecycle",
        }
        client.post(
            f"{base}/lease/acquire/",
            {"lease_seconds": 60},
            HTTP_IDEMPOTENCY_KEY="lifecycle-acquire",
            **common,
        )
        heartbeat = client.post(
            f"{base}/lease/heartbeat/",
            {"lease_seconds": 60},
            HTTP_IDEMPOTENCY_KEY="lifecycle-heartbeat",
            **common,
        )
        paused = client.post(
            f"{base}/pause/",
            {},
            HTTP_IDEMPOTENCY_KEY="lifecycle-pause",
            **common,
        )
        resumed = client.post(
            f"{base}/resume/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="lifecycle-resume",
        )
        client.post(
            f"{base}/lease/acquire/",
            {"lease_seconds": 60},
            HTTP_IDEMPOTENCY_KEY="lifecycle-reacquire",
            **common,
        )
        canceled = client.post(
            f"{base}/cancel/",
            {},
            HTTP_IDEMPOTENCY_KEY="lifecycle-cancel",
            **common,
        )

    assert heartbeat.status_code == 200
    assert paused.json()["status"] == "paused"
    assert resumed.json()["status"] == "waiting"
    assert canceled.json()["status"] == "canceled"
    assert gateway.abort_calls
