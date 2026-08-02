import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import RequestFactory

from files import tasks
from files.models import EncodeProfile, Encoding, Media, VideoTrimRequest
from files.methods import create_video_trim_request
from files.services.storage_backend import legacy_processing_allowed, uses_aws_pipeline
from files.services.storage_backend import LegacyProcessingNotAllowed
from files.views.pages import trim_video
from tests.users.factories import UserFactory


def build_media(storage_backend):
    return Media(
        title="Pipeline guard",
        user=UserFactory.build(),
        friendly_token=f"guard-{storage_backend}",
        storage_backend=storage_backend,
        media_file="source/input.mp4",
    )


def persist_without_signals(media):
    media.user = UserFactory()
    Media.objects.bulk_create([media])
    return Media.objects.get(pk=media.pk)


@pytest.mark.django_db
def test_aws_media_uses_only_the_aws_pipeline():
    media = build_media("aws")

    assert uses_aws_pipeline(media)
    assert not legacy_processing_allowed(media)


@pytest.mark.django_db
def test_legacy_local_media_keeps_existing_processing_behavior():
    media = build_media("legacy_local")

    assert not uses_aws_pipeline(media)
    assert legacy_processing_allowed(media)


@pytest.mark.django_db(transaction=True)
def test_changing_aws_source_does_not_schedule_legacy_media_init():
    media = persist_without_signals(build_media("aws"))

    with (
        patch("files.tasks.media_init.apply_async") as scheduled,
        patch.object(Media, "media_init", return_value=False),
    ):
        media.media_file = "source/replacement.mp4"
        media.save(update_fields=["media_file"])

    scheduled.assert_not_called()


@pytest.mark.django_db
def test_aws_model_initialization_exits_before_local_media_inspection():
    media = persist_without_signals(build_media("aws"))

    with patch.object(Media, "set_media_type", side_effect=AssertionError("local access")):
        assert media.media_init() is False


@pytest.mark.django_db
def test_queued_legacy_tasks_exit_for_aws_media_before_local_access(settings):
    media = persist_without_signals(build_media("aws"))
    trim_request = VideoTrimRequest.objects.create(
        media=media,
        status="initial",
        video_action="replace",
        media_trim_style="no_encoding",
        timestamps=[],
    )
    settings.MP4HLS_COMMAND = "/bin/true"

    with (
        patch.object(Media, "set_media_type", side_effect=AssertionError("local access")),
        patch("files.tasks.tempfile.TemporaryDirectory", side_effect=AssertionError("local access")),
        patch("files.tasks.get_trim_timestamps", side_effect=AssertionError("local access")),
    ):
        assert tasks.media_init.run(media.friendly_token) is False
        assert tasks.produce_sprite_from_video.run(media.friendly_token) is False
        assert tasks.create_hls.run(media.friendly_token) is False
        assert tasks.post_trim_action.run(media.friendly_token) is False
        assert tasks.video_trim_task.run(trim_request.id) is False


@pytest.mark.django_db(transaction=True)
def test_aws_media_rejects_model_level_legacy_processing():
    media = persist_without_signals(build_media("aws"))

    with patch("files.tasks.produce_sprite_from_video.delay") as scheduled:
        assert media.produce_sprite_from_video() is False
    scheduled.assert_not_called()
    assert media.encode() is False


@pytest.mark.django_db
def test_aws_media_rejects_legacy_trim_request():
    media = persist_without_signals(build_media("aws"))

    with pytest.raises(LegacyProcessingNotAllowed):
        create_video_trim_request(media, {"segments": []})

    assert not VideoTrimRequest.objects.filter(media=media).exists()


@pytest.mark.django_db
def test_queued_encode_and_chunk_tasks_exit_for_aws_media():
    media = persist_without_signals(build_media("aws"))
    profile = EncodeProfile.objects.create(
        name="Guard profile",
        extension="mp4",
        resolution=360,
        codec="h264",
    )
    encoding = Encoding.objects.create(media=media, profile=profile)

    with patch("files.tasks.produce_ffmpeg_commands", side_effect=AssertionError("local access")):
        assert tasks.encode_media.run(media.friendly_token, profile.id, encoding.id) is False
        assert tasks.chunkize_media.run(media.friendly_token, [profile.id]) is False


@pytest.mark.django_db
def test_aws_encoding_completion_does_not_schedule_legacy_hls_or_trim():
    media = persist_without_signals(build_media("aws"))
    encoding = SimpleNamespace(
        status="success",
        chunk=False,
        profile=SimpleNamespace(extension="mp4", codec="h264"),
        media_file=SimpleNamespace(path="legacy/output.mp4"),
    )

    with (
        patch.object(Media, "set_encoding_status"),
        patch.object(Media, "save"),
        patch("files.tasks.create_hls.delay") as hls,
        patch("files.tasks.post_trim_action.delay") as trim,
    ):
        result = media.post_encode_actions(encoding=encoding, action="add")

    assert result is False
    hls.assert_not_called()
    trim.assert_not_called()


@pytest.mark.django_db
def test_new_aws_media_post_save_signal_skips_legacy_initialization():
    user = UserFactory()

    with (
        patch.object(Media, "media_init") as initialize,
        patch("files.methods.notify_users"),
        patch.object(Media, "update_search_vector"),
    ):
        Media.objects.create(
            title="AWS signal guard",
            user=user,
            friendly_token="aws-signal-guard",
            storage_backend="aws",
            media_file="source/input.mp4",
        )

    initialize.assert_not_called()


@pytest.mark.django_db
def test_aws_media_trim_endpoint_returns_stable_rejection(settings):
    settings.ALLOW_VIDEO_TRIMMER = True
    media = persist_without_signals(build_media("aws"))
    media.user.is_editor = True
    media.user.save(update_fields=["is_editor"])
    request = RequestFactory().post(
        f"/api/v1/media/{media.friendly_token}/trim_video",
        data=json.dumps({"segments": []}),
        content_type="application/json",
    )
    request.user = media.user

    with patch("files.views.pages.video_trim_task.delay") as scheduled:
        response = trim_video(request, media.friendly_token)

    assert response.status_code == 400
    assert json.loads(response.content) == {
        "success": False,
        "error": "AWS media cannot use legacy video trimming",
        "code": "legacy_processing_unavailable",
    }
    assert not VideoTrimRequest.objects.filter(media=media).exists()
    scheduled.assert_not_called()
