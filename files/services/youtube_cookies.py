"""Encrypted cookie storage with no plaintext cookie values in logs or fields."""

import base64
import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet
from django.conf import settings
from django.utils import timezone

from files.models import YouTubeCookieVersion


def _fernet():
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def validate_netscape_cookies(payload):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if not isinstance(payload, bytes) or len(payload) > 2 * 1024 * 1024:
        raise ValueError("cookies file is too large")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("cookies file must be UTF-8 text") from error
    rows = [line for line in text.splitlines() if line and not line.startswith("#")]
    if not rows or any(len(row.split("\t")) != 7 for row in rows):
        raise ValueError("cookies file must use Netscape format")
    return payload


def store_cookies(payload):
    payload = validate_netscape_cookies(payload)
    checksum = hashlib.sha256(payload).hexdigest()
    encrypted = _fernet().encrypt(payload)
    YouTubeCookieVersion.objects.filter(status=YouTubeCookieVersion.Status.ACTIVE).update(
        status=YouTubeCookieVersion.Status.RETIRED
    )
    return YouTubeCookieVersion.objects.create(
        encrypted_payload=encrypted,
        checksum=checksum,
        status=YouTubeCookieVersion.Status.ACTIVE,
    )


def latest_cookie():
    return YouTubeCookieVersion.objects.filter(status=YouTubeCookieVersion.Status.ACTIVE).order_by("-uploaded_at").first()


@contextmanager
def materialize_cookie(version=None, directory=None):
    version = version or latest_cookie()
    if version is None:
        yield None
        return
    directory = directory or settings.TEMP_DIRECTORY
    fd, path = tempfile.mkstemp(prefix="yt-cookies-", suffix=".txt", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(_fernet().decrypt(bytes(version.encrypted_payload)))
        YouTubeCookieVersion.objects.filter(pk=version.pk).update(last_used_at=timezone.now())
        yield Path(path)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
