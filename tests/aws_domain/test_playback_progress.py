import uuid

import pytest
from rest_framework.test import APIClient

from files.models import Media, MediaAssetVersion, MediaPlaybackProgress
from tests.users.factories import UserFactory


@pytest.fixture
def playback_media():
    media = Media.objects.create(
        title="Playback test",
        user=UserFactory(),
        friendly_token=f"playback-{uuid.uuid4()}",
        storage_backend="aws",
        media_file="originals/playback/source.mp4",
    )
    version = MediaAssetVersion.objects.create(media=media, manifest_key="candidates/playback/master.m3u8")
    return media, version


@pytest.mark.django_db
def test_playback_progress_is_bound_to_authenticated_user_and_asset_version(playback_media):
    media, version = playback_media
    user = UserFactory()
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.put(
        f"/api/v1/media/{media.pk}/playback-progress/",
        {"position_seconds": 42, "duration_seconds": 120, "asset_version_id": str(version.pk)},
        format="json",
    )

    assert response.status_code == 200
    progress = MediaPlaybackProgress.objects.get(media=media, user=user)
    assert progress.position_seconds == 42
    assert progress.asset_version_id == version.pk


@pytest.mark.django_db
def test_playback_progress_rejects_anonymous_requests(playback_media):
    media, _ = playback_media
    response = APIClient().get(f"/api/v1/media/{media.pk}/playback-progress/")

    assert response.status_code in (401, 403)
