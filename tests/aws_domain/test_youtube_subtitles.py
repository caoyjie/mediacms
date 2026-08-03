import pytest
import yt_dlp
from django.utils import timezone

from files.models import Media, MediaIngestionJob, MediaJobCheckpoint
from files.services.youtube import CaptionTrack, classify_ytdlp_error, discovered_caption_tracks, fetch_caption_text, normalize_youtube_url, choose_caption_tracks
from files.services.subtitles import (
    SubtitleCue,
    build_bilingual_webvtt,
    normalize_caption_payload,
    normalize_webvtt,
    parse_webvtt,
)
from files.services.youtube_cookies import materialize_cookie, store_cookies
from files.services.youtube_jobs import start_youtube_job
from files.processing_tasks import discover_youtube_metadata
from tests.users.factories import UserFactory


def test_normalize_youtube_url_accepts_single_video_and_rejects_playlist():
    assert normalize_youtube_url("https://www.youtube.com/watch?v=abc123") == "abc123"
    assert normalize_youtube_url("youtu.be/abc123?t=4") == "abc123"
    with pytest.raises(ValueError, match="single video"):
        normalize_youtube_url("https://www.youtube.com/playlist?list=PL123")


def test_caption_selection_prefers_manual_then_language_variants():
    tracks = choose_caption_tracks(
        {
            "zh-Hans": [{"url": "zh-auto", "kind": "automatic"}],
            "zh": [{"url": "zh-manual", "kind": "manual"}],
            "en-US": [{"url": "en-auto", "kind": "automatic"}],
        }
    )
    assert tracks["zh"].url == "zh-manual"
    assert tracks["en"].url == "en-auto"


def test_discovered_tracks_do_not_replace_manual_with_automatic():
    tracks = discovered_caption_tracks({
        "subtitles": {"en": [{"url": "manual"}]},
        "automatic_captions": {"en": [{"url": "automatic"}]},
    })
    assert choose_caption_tracks(tracks)["en"].url == "manual"


def test_webvtt_normalization_and_bilingual_matching_is_time_based():
    zh = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n你好\n"
    en = "WEBVTT\n\n00:00:00.500 --> 00:00:02.500\nhello\n"
    assert normalize_webvtt(zh).startswith("WEBVTT\n\n00:00:00.000 --> 00:00:02.000")
    cues = parse_webvtt(zh)
    assert cues == [SubtitleCue(0.0, 2.0, "你好")]
    bilingual = build_bilingual_webvtt(parse_webvtt(zh), parse_webvtt(en))
    assert "你好 / hello" in bilingual


def test_json3_caption_payload_is_converted_to_webvtt():
    payload = '{"events":[{"tStartMs":0,"dDurationMs":1500,"segs":[{"utf8":"hello"}]}]}'
    result = normalize_caption_payload(payload)
    assert result.startswith("WEBVTT")
    assert "hello" in result


def test_subtitle_failure_classification_is_safe():
    assert classify_ytdlp_error("Sign in to confirm your age") == "cookies"
    assert classify_ytdlp_error("The provided YouTube account cookies are no longer valid") == "cookies"
    assert classify_ytdlp_error("HTTP Error 429: Too Many Requests") == "retryable"
    assert classify_ytdlp_error("no subtitles available") == "unavailable"
    assert classify_ytdlp_error("some unknown failure") == "unknown"


def test_extract_info_reads_django_settings_without_name_error(monkeypatch):
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            assert url == "https://www.youtube.com/watch?v=abc123"
            assert download is False
            return {"id": "abc123", "title": "Fixture"}

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYoutubeDL)
    from files.services.youtube import extract_info

    result = extract_info("https://www.youtube.com/watch?v=abc123")

    assert result["id"] == "abc123"
    assert captured["noplaylist"] is True


def test_caption_fetch_uses_utf8_and_rejects_oversized_payload():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            assert limit == 4 * 1024 * 1024 + 1
            return b"WEBVTT\n"

    captured = []

    def opener(request, timeout):
        captured.append((request.full_url, timeout))
        return Response()

    assert fetch_caption_text(CaptionTrack("https://example.test/caption", "en", "manual"), opener=opener) == "WEBVTT\n"
    assert captured == [("https://example.test/caption", 30)]


@pytest.mark.django_db
def test_cookies_are_encrypted_and_materialized_as_0600_then_removed(tmp_path):
    payload = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsecret\n"
    version = store_cookies(payload)
    assert bytes(version.encrypted_payload) != payload
    with materialize_cookie(version, directory=tmp_path) as path:
        assert path.read_bytes() == payload
        assert path.stat().st_mode & 0o777 == 0o600
        materialized = path
    assert not materialized.exists()


@pytest.mark.django_db
def test_uploading_the_same_cookie_file_is_idempotent():
    payload = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsecret\n"

    first = store_cookies(payload)
    second = store_cookies(payload)

    assert second.pk == first.pk
    assert second.status == second.Status.ACTIVE
    assert second.__class__.objects.count() == 1


@pytest.mark.django_db
def test_start_youtube_job_releases_metadata_preview_for_import():
    user = UserFactory(username="youtube-start-owner")
    media = Media.objects.create(
        title="Preview",
        user=user,
        friendly_token="youtube-start-preview",
        storage_backend="aws",
        media_file="aws-source/pending-upload",
    )
    job = MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot=media.title,
        source_type="youtube",
        status="running",
        stage="metadata_ready",
        source_metadata={"url": "https://www.youtube.com/watch?v=abc123", "discovered": {"title": "Preview"}},
        queued_at=timezone.now(),
    )

    started = start_youtube_job(job.id)

    assert started.id == job.id
    started.refresh_from_db()
    assert started.status == "queued"
    assert started.source_metadata["import_requested"] is True


@pytest.mark.django_db
def test_metadata_task_persists_preview_without_import_side_effects(monkeypatch):
    user = UserFactory(username="youtube-metadata-owner")
    media = Media.objects.create(
        title="Preview",
        user=user,
        friendly_token="youtube-metadata-preview",
        storage_backend="aws",
        media_file="aws-source/pending-upload",
    )
    job = MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot=media.title,
        source_type="youtube",
        source_metadata={"url": "https://www.youtube.com/watch?v=abc123"},
        status="queued",
        stage="metadata_pending",
        queued_at=timezone.now(),
    )

    from files.services.youtube_import import YouTubeMetadata

    monkeypatch.setattr(
        "files.processing_tasks.discover",
        lambda url: (YouTubeMetadata("abc123", "Found", "Description", 42, "https://img.example/poster.jpg"), {"subtitles": {"en": [{"url": "https://caption.example/en", "kind": "manual"}]}}),
    )

    result = discover_youtube_metadata.run(str(job.id))

    job.refresh_from_db()
    assert result["stage"] == "metadata_ready"
    assert job.stage == "metadata_ready"
    assert job.source_metadata["discovered"]["title"] == "Found"
    assert MediaJobCheckpoint.objects.filter(attempt__job=job, name="metadata").count() == 1
