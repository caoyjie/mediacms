import hashlib
import json

from django.test import Client, TestCase, override_settings

from files.tests import create_account


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@override_settings(MEDIACMS_IDENTITY_TOKEN_HASH=token_hash("identity-secret"))
class SessionUserApiTest(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.password = "password-for-test"
        self.user = create_account(username="alice", password=self.password)

    def test_private_login_returns_identity_without_session(self) -> None:
        response = self.client.post(
            "/internal/api/auth/login/",
            json.dumps({"username": "alice", "password": self.password}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer identity-secret",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": str(self.user.id),
                "username": "alice",
                "is_active": True,
                "session_version": 1,
            },
        )
        self.assertNotIn("sessionid", self.client.cookies)

    def test_private_login_requires_identity_token(self) -> None:
        response = self.client.post(
            "/internal/api/auth/login/",
            json.dumps({"username": "alice", "password": self.password}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)

    def test_identity_validation_returns_current_identity(self) -> None:
        response = self.client.post(
            "/internal/api/identity/validate/",
            json.dumps({"id": str(self.user.id), "session_version": 1}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer identity-secret",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": str(self.user.id),
                "username": "alice",
                "is_active": True,
                "session_version": 1,
            },
        )

    def test_identity_validation_requires_identity_token(self) -> None:
        response = self.client.post(
            "/internal/api/identity/validate/",
            json.dumps({"id": str(self.user.id), "session_version": 1}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)

    def test_version_mismatch_is_rejected(self) -> None:
        response = self.client.post(
            "/internal/api/identity/validate/",
            json.dumps({"id": str(self.user.id), "session_version": 2}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer identity-secret",
        )

        self.assertEqual(response.status_code, 401)

    def test_inactive_user_is_rejected(self) -> None:
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self.client.post(
            "/internal/api/identity/validate/",
            json.dumps(
                {
                    "id": str(self.user.id),
                    "session_version": self.user.session_version,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer identity-secret",
        )

        self.assertEqual(response.status_code, 403)
