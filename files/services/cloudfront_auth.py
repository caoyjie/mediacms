"""CloudFront signed-cookie issuance for private media."""

import json
import time
from urllib.parse import parse_qs, urlsplit

from django.conf import settings

COOKIE_NAMES = ("CloudFront-Policy", "CloudFront-Signature", "CloudFront-Key-Pair-Id")


def _configured():
    domain = getattr(settings, "AWS_CLOUDFRONT_DOMAIN", "").strip()
    key_pair_id = getattr(settings, "AWS_CLOUDFRONT_KEY_PAIR_ID", "").strip()
    private_key = getattr(settings, "AWS_CLOUDFRONT_PRIVATE_KEY", "")
    if not domain or not key_pair_id or not private_key:
        raise RuntimeError("CloudFront signed-cookie configuration is incomplete")
    return domain.rstrip("/"), key_pair_id, private_key


def issue_signed_cookies(*, ttl_seconds=None, now=None, signer=None):
    domain, key_pair_id, private_key = _configured()
    ttl = int(ttl_seconds or getattr(settings, "AWS_CLOUDFRONT_COOKIE_TTL_SECONDS", 3600))
    if ttl < 60 or ttl > 86400:
        raise ValueError("CloudFront cookie TTL is outside the safe range")
    epoch = int(now if now is not None else time.time())
    policy = json.dumps({"Statement": [{
        "Resource": f"https://{domain}/media/*",
        "Condition": {"DateLessThan": {"AWS:EpochTime": epoch + ttl}},
    }]}, separators=(",", ":"))
    if signer is None:
        from boto3.cloudfront import CloudFrontSigner
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        def rsa_sign(message):
            key = serialization.load_pem_private_key(private_key.encode(), password=None)
            return key.sign(message, padding.PKCS1v15(), hashes.SHA1())

        signer = CloudFrontSigner(key_pair_id, rsa_sign)
    signed = signer.generate_presigned_url(
        f"https://{domain}/media/placeholder",
        date_less_than=epoch + ttl,
        policy=policy,
    )
    query = parse_qs(urlsplit(signed).query)
    return {
        "CloudFront-Policy": query.get("Policy", [""])[0],
        "CloudFront-Signature": query.get("Signature", [""])[0],
        "CloudFront-Key-Pair-Id": query.get("Key-Pair-Id", [key_pair_id])[0],
        "expires_at": epoch + ttl,
    }


def set_cookie_headers(response, cookies, *, max_age=None):
    age = max_age if max_age is not None else getattr(settings, "AWS_CLOUDFRONT_COOKIE_TTL_SECONDS", 3600)
    for name in COOKIE_NAMES:
        response.set_cookie(name, cookies.get(name, ""), max_age=age, secure=True, httponly=True, samesite="Lax", domain=getattr(settings, "AWS_CLOUDFRONT_COOKIE_DOMAIN", None) or None, path="/")
    return response


def clear_cookie_headers(response):
    for name in COOKIE_NAMES:
        response.delete_cookie(name, domain=getattr(settings, "AWS_CLOUDFRONT_COOKIE_DOMAIN", None) or None, path="/")
    return response
