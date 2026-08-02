from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from files.models import BrowserUploadSession
from tests.aws_ingestion.test_upload_sessions import RecordingUploadGateway
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
