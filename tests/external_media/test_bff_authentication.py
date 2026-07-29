import hashlib

from django.test import Client, TestCase, override_settings
from rest_framework.authtoken.models import Token

from files.tests import create_account


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@override_settings(
    MEDIACMS_BFF_TOKEN_HASH=token_hash("current-bff-token"),
    MEDIACMS_BFF_PREVIOUS_TOKEN_HASH=token_hash("previous-bff-token"),
)
class BffAuthenticationTest(TestCase):
    def setUp(self) -> None:
        self.user = create_account(username="alice", password="password")
        self.client = Client()

    def request(
        self,
        token: str,
        *,
        user_id: str | None = None,
        version: int | str | None = None,
    ):
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {token}",
            "HTTP_X_MEDIA_PLATFORM_USER_ID": user_id or str(self.user.id),
            "HTTP_X_MEDIA_PLATFORM_SESSION_VERSION": str(
                self.user.session_version if version is None else version
            ),
        }
        return self.client.get("/api/v1/whoami", **headers)

    def test_current_token_restores_real_user(self) -> None:
        response = self.request("current-bff-token")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["username"], "alice")

    def test_previous_token_supports_rotation_window(self) -> None:
        response = self.request("previous-bff-token")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["username"], "alice")

    def test_wrong_bearer_token_fails_closed(self) -> None:
        response = self.request("wrong-token")

        self.assertEqual(response.status_code, 401)

    def test_session_version_mismatch_is_rejected(self) -> None:
        response = self.request("current-bff-token", version=999)

        self.assertEqual(response.status_code, 401)

    def test_inactive_user_is_rejected(self) -> None:
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self.request("current-bff-token")

        self.assertEqual(response.status_code, 403)

    def test_missing_and_malformed_identity_headers_are_rejected(self) -> None:
        missing = self.client.get(
            "/api/v1/whoami",
            HTTP_AUTHORIZATION="Bearer current-bff-token",
        )
        malformed = self.request("current-bff-token", version="not-an-integer")

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(malformed.status_code, 401)

    def test_missing_user_is_rejected(self) -> None:
        response = self.request("current-bff-token", user_id="999999999")

        self.assertEqual(response.status_code, 401)

    @override_settings(
        MEDIACMS_BFF_TOKEN_HASH="",
        MEDIACMS_BFF_PREVIOUS_TOKEN_HASH="",
    )
    def test_empty_configuration_disables_bff_authentication(self) -> None:
        response = self.request("current-bff-token")

        self.assertEqual(response.status_code, 401)

    def test_existing_django_session_authentication_still_works(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get("/api/v1/whoami")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["username"], "alice")

    def test_existing_drf_token_authentication_still_works(self) -> None:
        token = Token.objects.create(user=self.user)

        response = self.client.get(
            "/api/v1/whoami",
            HTTP_AUTHORIZATION=f"Token {token.key}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["username"], "alice")
