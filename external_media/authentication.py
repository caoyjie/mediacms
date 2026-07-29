import hashlib
import hmac

from django.conf import settings


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
