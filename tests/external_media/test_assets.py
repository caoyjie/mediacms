from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import TestCase

from files.models import Language, Media, Subtitle
from files.tests import create_account


class ExternalAssetTest(TestCase):
    def setUp(self) -> None:
        self.user = create_account(username="asset-owner")
        self.language = Language.objects.create(code="en", title="English")
        self.media_init_patcher = patch("files.models.media.Media.media_init")
        self.media_init_patcher.start()
        self.addCleanup(self.media_init_patcher.stop)
        self.media = Media.objects.create(
            user=self.user,
            title="External video",
            media_file=SimpleUploadedFile("video.mp4", b"video"),
        )

    def test_external_hls_wins_over_local_hls(self) -> None:
        self.media.external_hls_url = "https://media.ygcyj.xin/media/m1/hls/master.m3u8"
        self.media.save()

        self.assertEqual(self.media.hls_info["master_file"], self.media.external_hls_url)

    def test_external_hls_is_complete_without_local_encodings(self) -> None:
        self.media.external_hls_url = (
            "https://media.ygcyj.xin/media/m1/hls/master.m3u8"
        )
        self.media.encoding_status = "pending"
        self.media.save()

        self.media.set_encoding_status()

        self.assertEqual(self.media.encoding_status, "success")

    def test_external_cover_wins_over_local_thumbnail(self) -> None:
        self.media.external_cover_url = "https://media.ygcyj.xin/media/m1/images/cover.jpg"
        self.media.save()

        self.assertEqual(self.media.thumbnail_url, self.media.external_cover_url)

    def test_external_poster_wins_over_local_poster(self) -> None:
        self.media.external_poster_url = "https://media.ygcyj.xin/media/m1/images/poster.jpg"
        self.media.save()

        self.assertEqual(self.media.poster_url, self.media.external_poster_url)

    def test_external_subtitle_url_is_serialized(self) -> None:
        subtitle = Subtitle.objects.create(
            media=self.media,
            language=self.language,
            user=self.user,
            subtitle_file=SimpleUploadedFile("english.vtt", b"WEBVTT"),
            external_url="https://media.ygcyj.xin/media/m1/subtitles/en.vtt",
        )

        self.assertEqual(self.media.subtitles_info[0]["src"], subtitle.external_url)

    def test_backend_media_id_is_unique(self) -> None:
        self.media.backend_media_id = "backend-1"
        self.media.save()
        duplicate = Media(
            user=self.user,
            title="Duplicate",
            backend_media_id="backend-1",
            media_file=SimpleUploadedFile("video-2.mp4", b"video"),
        )

        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_external_asset_delete_does_not_remove_remote_object(self) -> None:
        self.media.external_poster_url = "https://media.ygcyj.xin/media/m1/images/poster.jpg"
        self.media.save()
        self.media.media_file = None

        with patch("files.helpers.rm_file") as rm_file:
            self.media.delete()

        rm_file.assert_not_called()

    def test_external_asset_rejects_http_and_foreign_host(self) -> None:
        for value in (
            "http://media.ygcyj.xin/media/m1/hls/master.m3u8",
            "https://example.com/media/m1/hls/master.m3u8",
        ):
            self.media.external_hls_url = value
            with self.assertRaises(ValidationError):
                self.media.full_clean()
