import hashlib
import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from files.models import Language, Media, Subtitle
from files.tests import create_account


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@override_settings(MEDIACMS_PUBLISHING_TOKEN_HASH=token_hash("publishing-secret"))
class ExternalMediaApiTest(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.owner = create_account(username="publisher-owner")
        self.payload = {
            "backend_media_id": "backend-asset-1",
            "owner_username": self.owner.username,
            "title": "Published video",
            "description": "Version A",
            "external_hls_url": "https://media.ygcyj.xin/media/m1/hls/master.m3u8",
            "external_poster_url": "https://media.ygcyj.xin/media/m1/images/poster.jpg",
            "external_cover_url": "https://media.ygcyj.xin/media/m1/images/cover.jpg",
            "version": 1,
        }
        self.media_init_patcher = patch("files.models.media.Media.media_init")
        self.media_init_mock = self.media_init_patcher.start()
        self.addCleanup(self.media_init_patcher.stop)

    def post(self, payload=None):
        return self.client.post(
            "/internal/api/external-media/",
            json.dumps(payload or self.payload),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer publishing-secret",
        )

    def test_publishing_requires_scoped_token(self) -> None:
        response = self.client.post(
            "/internal/api/external-media/",
            data=json.dumps(self.payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)

    def test_post_is_idempotent_for_backend_media_id(self) -> None:
        first = self.post()
        second = self.post()

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(Media.objects.filter(backend_media_id="backend-asset-1").count(), 1)
        self.media_init_mock.assert_not_called()

    @override_settings(
        DO_NOT_TRANSCODE_VIDEO=True,
        SHOW_ORIGINAL_MEDIA=True,
    )
    def test_external_media_detail_does_not_require_a_local_file(self) -> None:
        created = self.post()
        media = Media.objects.get(id=created.json()["id"])

        response = self.client.get(
            f"/api/v1/media/{media.friendly_token}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["hls_info"]["master_file"],
            self.payload["external_hls_url"],
        )
        self.assertEqual(response.json()["encodings_info"], {})
        self.assertIsNone(response.json()["original_media_url"])

    def test_post_upserts_external_subtitles_idempotently(self) -> None:
        payload = {
            **self.payload,
            "subtitles": [
                {
                    "language": "en",
                    "label": "English",
                    "external_url": (
                        "https://media.ygcyj.xin/media/m1/"
                        "subtitles/en/normalized.vtt"
                    ),
                }
            ],
        }

        first = self.post(payload)
        second = self.post(payload)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        media = Media.objects.get(backend_media_id="backend-asset-1")
        self.assertEqual(media.subtitles.count(), 1)
        self.assertEqual(second.json()["subtitles"], payload["subtitles"])

    def test_patch_removes_only_omitted_external_subtitles(self) -> None:
        created = self.post(
            {
                **self.payload,
                "subtitles": [
                    {
                        "language": "en",
                        "label": "English",
                        "external_url": (
                            "https://media.ygcyj.xin/media/m1/"
                            "subtitles/en/normalized.vtt"
                        ),
                    }
                ],
            }
        )
        media = Media.objects.get(backend_media_id="backend-asset-1")
        local_language = Language.objects.create(code="fr", title="French")
        Subtitle.objects.create(
            media=media,
            language=local_language,
            user=self.owner,
            subtitle_file=SimpleUploadedFile("local.vtt", b"WEBVTT"),
            external_url=(
                "https://media.ygcyj.xin/media/m1/"
                "subtitles/fr/normalized.vtt"
            ),
        )

        response = self.client.patch(
            "/internal/api/external-media/backend-asset-1/",
            data=json.dumps(
                {
                    "version": created.json()["version"],
                    "subtitles": [],
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer publishing-secret",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(media.subtitles.filter(language__code="en").exists())
        local = media.subtitles.get(language__code="fr")
        self.assertTrue(bool(local.subtitle_file))
        self.assertIsNone(local.external_url)
        self.assertEqual(response.json()["subtitles"], [])

    def test_omitted_subtitles_does_not_change_existing_rows(self) -> None:
        created = self.post(
            {
                **self.payload,
                "subtitles": [
                    {
                        "language": "en",
                        "label": "English",
                        "external_url": (
                            "https://media.ygcyj.xin/media/m1/"
                            "subtitles/en/normalized.vtt"
                        ),
                    }
                ],
            }
        )

        response = self.client.patch(
            "/internal/api/external-media/backend-asset-1/",
            data=json.dumps(
                {
                    "version": created.json()["version"],
                    "description": "Version B",
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer publishing-secret",
        )

        self.assertEqual(response.status_code, 200)
        media = Media.objects.get(backend_media_id="backend-asset-1")
        self.assertEqual(media.subtitles.count(), 1)
        self.assertEqual(response.json()["subtitles"][0]["language"], "en")

    def test_subtitle_payload_rejects_duplicates_and_unsafe_urls_atomically(
        self,
    ) -> None:
        valid_url = (
            "https://media.ygcyj.xin/media/m1/subtitles/en/normalized.vtt"
        )
        invalid_sets = (
            [
                {
                    "language": "en",
                    "label": "English",
                    "external_url": valid_url,
                },
                {
                    "language": "EN",
                    "label": "English duplicate",
                    "external_url": valid_url,
                },
            ],
            [
                {
                    "language": "en",
                    "label": "English",
                    "external_url": (
                        "http://media.ygcyj.xin/media/m1/"
                        "subtitles/en/normalized.vtt"
                    ),
                }
            ],
            [
                {
                    "language": "en",
                    "label": "English",
                    "external_url": (
                        "https://example.com/media/m1/"
                        "subtitles/en/normalized.vtt"
                    ),
                }
            ],
            [
                {
                    "language": "en",
                    "label": "English",
                    "external_url": "https://media.ygcyj.xin/not-media/en.vtt",
                }
            ],
        )

        for subtitles in invalid_sets:
            response = self.post({**self.payload, "subtitles": subtitles})

            self.assertEqual(response.status_code, 400)
            self.assertFalse(
                Media.objects.filter(
                    backend_media_id="backend-asset-1"
                ).exists()
            )
            self.assertEqual(Subtitle.objects.count(), 0)

    def test_patch_validation_rolls_back_parent_and_subtitle_changes(
        self,
    ) -> None:
        original_url = (
            "https://media.ygcyj.xin/media/m1/subtitles/en/normalized.vtt"
        )
        created = self.post(
            {
                **self.payload,
                "subtitles": [
                    {
                        "language": "en",
                        "label": "English",
                        "external_url": original_url,
                    }
                ],
            }
        )

        response = self.client.patch(
            "/internal/api/external-media/backend-asset-1/",
            data=json.dumps(
                {
                    "version": created.json()["version"],
                    "description": "Version B",
                    "subtitles": [
                        {
                            "language": "en",
                            "label": "English",
                            "external_url": (
                                "https://media.ygcyj.xin/media/m1/"
                                "subtitles/en/replacement.vtt"
                            ),
                        },
                        {
                            "language": "fr",
                            "label": "French",
                            "external_url": (
                                "https://example.com/media/m1/"
                                "subtitles/fr/normalized.vtt"
                            ),
                        },
                    ],
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer publishing-secret",
        )

        self.assertEqual(response.status_code, 400)
        media = Media.objects.get(backend_media_id="backend-asset-1")
        self.assertEqual(media.description, "Version A")
        self.assertEqual(media.subtitles.get().external_url, original_url)

    def test_publishing_rejects_http_and_foreign_asset_urls(self) -> None:
        for url in (
            "http://media.ygcyj.xin/media/m1/hls/master.m3u8",
            "https://example.com/media/m1/hls/master.m3u8",
        ):
            payload = {**self.payload, "external_hls_url": url}
            response = self.post(payload)

            self.assertEqual(response.status_code, 400)

    def test_patch_rejects_stale_version_without_overwriting_visible_asset(self) -> None:
        created = self.post()
        self.assertEqual(created.status_code, 201)

        response = self.client.patch(
            "/internal/api/external-media/backend-asset-1/",
            data=json.dumps(
                {
                    "description": "Version B",
                    "external_hls_url": "https://media.ygcyj.xin/media/m1/hls/version-b.m3u8",
                    "version": 0,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer publishing-secret",
        )

        self.assertEqual(response.status_code, 409)
        media = Media.objects.get(backend_media_id="backend-asset-1")
        self.assertEqual(media.description, "Version A")
        self.assertEqual(media.external_hls_url, self.payload["external_hls_url"])

    def test_patch_updates_assets_with_current_version(self) -> None:
        created = self.post()

        response = self.client.patch(
            f"/internal/api/external-media/{self.payload['backend_media_id']}/",
            data=json.dumps(
                {
                    "description": "Version B",
                    "external_hls_url": "https://media.ygcyj.xin/media/m1/hls/version-b.m3u8",
                    "version": created.json()["version"],
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer publishing-secret",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], 2)
        media = Media.objects.get(backend_media_id="backend-asset-1")
        self.assertEqual(media.description, "Version B")
        self.assertEqual(media.external_hls_url, "https://media.ygcyj.xin/media/m1/hls/version-b.m3u8")
