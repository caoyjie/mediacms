import hashlib
import hmac

from django.conf import settings
from rest_framework.authentication import SessionAuthentication

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


class SessionUserAuthentication(SessionAuthentication):
    """Authenticate the Django session before the view checks activity state."""

    def authenticate(self, request):
        user_id = request._request.session.get("_auth_user_id")
        if not user_id:
            return None
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
        self.enforce_csrf(request)
        return user, None
