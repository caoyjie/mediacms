from django.test import override_settings

from files.services.cloudfront_auth import COOKIE_NAMES, issue_signed_cookies


class FakeSigner:
    def generate_presigned_url(self, url, date_less_than, policy):
        return f"{url}?Policy=policy&Signature=sig&Key-Pair-Id=K1"


@override_settings(
    AWS_CLOUDFRONT_DOMAIN="d111.cloudfront.net",
    AWS_CLOUDFRONT_KEY_PAIR_ID="K1",
    AWS_CLOUDFRONT_PRIVATE_KEY="unused",
)
def test_cloudfront_cookie_issue_uses_stable_media_scope_and_expiry():
    cookies = issue_signed_cookies(ttl_seconds=3600, now=100, signer=FakeSigner())
    assert tuple(cookies[name] for name in COOKIE_NAMES) == ("policy", "sig", "K1")
    assert cookies["expires_at"] == 3700
