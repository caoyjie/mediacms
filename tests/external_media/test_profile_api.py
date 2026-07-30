import base64
import hashlib
import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from files.tests import create_account
from users.models import User


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
GIF_1X1 = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
)


@override_settings(
    MEDIACMS_BFF_TOKEN_HASH=token_hash("current-bff-token"),
    MEDIACMS_BFF_PREVIOUS_TOKEN_HASH="",
    MEDIA_ROOT="/tmp/mediacms-profile-api-tests",
)
class PrivateProfileApiTest(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.password = "password-for-test"
        self.user = create_account(
            username="alice",
            password=self.password,
            email="alice@example.com",
            name="Alice",
            description="Field recordist",
        )
        self.user.notification_on_comments = True
        self.user.save(update_fields=["notification_on_comments"])
        self.other = create_account(
            username="bob",
            password="other-password",
            email="bob@example.com",
        )

    def bff_headers(
        self,
        *,
        token: str = "current-bff-token",
        user_id: str | None = None,
        version: int | str | None = None,
    ) -> dict[str, str]:
        return {
            "HTTP_AUTHORIZATION": f"Bearer {token}",
            "HTTP_X_MEDIA_PLATFORM_USER_ID": user_id or str(self.user.id),
            "HTTP_X_MEDIA_PLATFORM_SESSION_VERSION": str(
                self.user.session_version if version is None else version
            ),
        }

    def patch_profile(self, payload: object, **headers):
        return self.client.patch(
            "/internal/api/profile/",
            json.dumps(payload),
            content_type="application/json",
            **(headers or self.bff_headers()),
        )

    def test_current_profile_returns_exact_private_allowlist(self) -> None:
        response = self.client.get(
            "/internal/api/profile/",
            **self.bff_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()),
            {
                "id",
                "username",
                "name",
                "description",
                "email",
                "notification_on_comments",
                "thumbnail_url",
                "session_version",
            },
        )
        self.assertEqual(
            response.json(),
            {
                "id": str(self.user.id),
                "username": "alice",
                "name": "Alice",
                "description": "Field recordist",
                "email": "alice@example.com",
                "notification_on_comments": True,
                "thumbnail_url": "http://testserver/media/userlogos/user.jpg",
                "session_version": 1,
            },
        )

    def test_profile_endpoints_require_bff_authentication(self) -> None:
        requests = [
            self.client.get("/internal/api/profile/"),
            self.patch_profile({"name": "No token"}, HTTP_AUTHORIZATION="Bearer wrong"),
            self.client.post(
                "/internal/api/profile/logo/",
                {"logo": self.png_upload()},
            ),
            self.client.post(
                "/internal/api/profile/password/",
                json.dumps(
                    {
                        "current_password": self.password,
                        "new_password": "A-strong-replacement-password-2026",
                    }
                ),
                content_type="application/json",
            ),
        ]

        self.assertTrue(all(response.status_code == 401 for response in requests))

    def test_missing_identity_stale_version_and_inactive_user_fail_closed(self) -> None:
        missing = self.client.get(
            "/internal/api/profile/",
            HTTP_AUTHORIZATION="Bearer current-bff-token",
        )
        stale = self.client.get(
            "/internal/api/profile/",
            **self.bff_headers(version=999),
        )
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        inactive = self.client.get(
            "/internal/api/profile/",
            **self.bff_headers(version=self.user.session_version),
        )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(stale.status_code, 401)
        self.assertEqual(inactive.status_code, 403)

    def test_patch_updates_only_allowlisted_fields_on_authenticated_user(self) -> None:
        response = self.patch_profile(
            {
                "name": "Alice Updated",
                "description": "New bio",
                "email": "new-alice@example.com",
                "notification_on_comments": False,
                "username": "mallory",
                "is_active": False,
                "is_manager": True,
                "session_version": 900,
            }
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.user.name, "Alice Updated")
        self.assertEqual(self.user.description, "New bio")
        self.assertEqual(self.user.email, "new-alice@example.com")
        self.assertFalse(self.user.notification_on_comments)
        self.assertEqual(self.user.username, "alice")
        self.assertTrue(self.user.is_active)
        self.assertFalse(self.user.is_manager)
        self.assertEqual(self.user.session_version, 1)
        self.assertEqual(self.other.email, "bob@example.com")

    def test_profile_patch_validates_lengths_email_uniqueness_and_boolean_type(self) -> None:
        cases = [
            {"name": "x" * 251},
            {"description": "x" * 10_001},
            {"email": "not-an-email"},
            {"email": "BOB@example.com"},
            {"notification_on_comments": "false"},
            {"notification_on_comments": 0},
        ]

        for payload in cases:
            with self.subTest(payload=next(iter(payload))):
                response = self.patch_profile(payload)
                self.assertEqual(response.status_code, 400)

    def png_upload(
        self,
        *,
        content: bytes = PNG_1X1,
        content_type: str = "image/png",
        name: str = "avatar.png",
    ) -> SimpleUploadedFile:
        return SimpleUploadedFile(name, content, content_type=content_type)

    def test_logo_accepts_decoded_png_and_returns_current_profile(self) -> None:
        response = self.client.post(
            "/internal/api/profile/logo/",
            {"logo": self.png_upload()},
            **self.bff_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.logo.name, "userlogos/user.jpg")
        self.assertEqual(response.json()["username"], "alice")
        self.assertIn("/media/userlogos/", response.json()["thumbnail_url"])

    def test_logo_rejects_missing_unsupported_mislabeled_and_oversized_files(self) -> None:
        cases = [
            {},
            {
                "logo": self.png_upload(
                    content_type="text/plain",
                )
            },
            {
                "logo": self.png_upload(
                    content=GIF_1X1,
                    content_type="image/png",
                )
            },
            {
                "logo": self.png_upload(
                    content=PNG_1X1 + b"x" * (2 * 1024 * 1024),
                )
            },
        ]

        for payload in cases:
            with self.subTest(payload=bool(payload)):
                response = self.client.post(
                    "/internal/api/profile/logo/",
                    payload,
                    **self.bff_headers(),
                )
                self.assertEqual(response.status_code, 400)

    def test_password_change_requires_current_password_and_django_validation(self) -> None:
        wrong = self.client.post(
            "/internal/api/profile/password/",
            json.dumps(
                {
                    "current_password": "wrong-password",
                    "new_password": "A-strong-replacement-password-2026",
                }
            ),
            content_type="application/json",
            **self.bff_headers(),
        )
        weak = self.client.post(
            "/internal/api/profile/password/",
            json.dumps(
                {
                    "current_password": self.password,
                    "new_password": "123",
                }
            ),
            content_type="application/json",
            **self.bff_headers(),
        )

        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(weak.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))
        self.assertEqual(self.user.session_version, 1)

    def test_password_change_invalidates_the_current_session_version(self) -> None:
        response = self.client.post(
            "/internal/api/profile/password/",
            json.dumps(
                {
                    "current_password": self.password,
                    "new_password": "A-strong-replacement-password-2026",
                }
            ),
            content_type="application/json",
            **self.bff_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(response.json(), {"session_version": 2})
        self.assertEqual(self.user.session_version, 2)
        self.assertTrue(
            self.user.check_password("A-strong-replacement-password-2026")
        )

    def test_password_change_rolls_back_password_and_version_together(self) -> None:
        with patch.object(
            User,
            "refresh_from_db",
            side_effect=RuntimeError("simulated refresh failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    "/internal/api/profile/password/",
                    json.dumps(
                        {
                            "current_password": self.password,
                            "new_password": "A-strong-replacement-password-2026",
                        }
                    ),
                    content_type="application/json",
                    **self.bff_headers(),
                )

        self.user.refresh_from_db()
        self.assertEqual(self.user.session_version, 1)
        self.assertTrue(self.user.check_password(self.password))
