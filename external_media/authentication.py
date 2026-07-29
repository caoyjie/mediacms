import hashlib
import hmac

from django.conf import settings
from rest_framework import exceptions, status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import APIException

from users.models import User


def bearer_token(request) -> str | None:
    value = request.headers.get("Authorization", "")
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def has_identity_scope(request) -> bool:
    token = bearer_token(request)
    expected = getattr(settings, "MEDIACMS_IDENTITY_TOKEN_HASH", "")
    if not token or not expected:
        return False
    actual = hashlib.sha256(token.encode()).hexdigest()
    return hmac.compare_digest(actual, expected)


def has_publishing_scope(request) -> bool:
    token = bearer_token(request)
    expected = getattr(settings, "MEDIACMS_PUBLISHING_TOKEN_HASH", "")
    if not token or not expected:
        return False
    actual = hashlib.sha256(token.encode()).hexdigest()
    return hmac.compare_digest(actual, expected)


def has_bff_scope(token: str) -> bool:
    actual = hashlib.sha256(token.encode()).hexdigest()
    expected_hashes = (
        getattr(settings, "MEDIACMS_BFF_TOKEN_HASH", ""),
        getattr(settings, "MEDIACMS_BFF_PREVIOUS_TOKEN_HASH", ""),
    )
    matches = False
    for expected in expected_hashes:
        if expected:
            matches = hmac.compare_digest(actual, expected) or matches
    return matches


class InactiveBffUser(APIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Inactive user"
    default_code = "inactive_user"


class BffUserAuthentication(BaseAuthentication):
    def authenticate_header(self, request) -> str | None:
        value = request.headers.get("Authorization", "")
        scheme, _, _ = value.partition(" ")
        return "Bearer" if scheme.lower() == "bearer" else None

    def authenticate(self, request):
        value = request.headers.get("Authorization", "")
        if not value:
            return None

        scheme, _, token = value.partition(" ")
        if scheme.lower() != "bearer":
            return None
        if not token or not has_bff_scope(token):
            raise exceptions.AuthenticationFailed("Invalid BFF credential")

        user_id = request.headers.get("X-Media-Platform-User-Id", "")
        version = request.headers.get("X-Media-Platform-Session-Version", "")
        if not user_id or not version.isdecimal() or int(version) < 1:
            raise exceptions.AuthenticationFailed("Invalid BFF identity")

        try:
            user = User.objects.get(pk=user_id)
        except (TypeError, ValueError, User.DoesNotExist) as exc:
            raise exceptions.AuthenticationFailed("Invalid BFF identity") from exc

        if not user.is_active:
            raise InactiveBffUser()
        if user.session_version != int(version):
            raise exceptions.AuthenticationFailed("Invalid BFF identity")
        return user, None
