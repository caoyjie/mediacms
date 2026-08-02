from types import SimpleNamespace

from django.test import override_settings

from files.services.asset_urls import cloudfront_asset_url


@override_settings(AWS_CLOUDFRONT_DOMAIN="d111.cloudfront.net")
def test_asset_url_is_versioned_and_does_not_expose_s3_key():
    media = SimpleNamespace(pk="m1", active_asset_version_id="v1")
    asset = SimpleNamespace(version_id="v1", s3_key="candidates/m1/v1/master.m3u8")
    assert cloudfront_asset_url(media, asset, filename="master.m3u8") == "https://d111.cloudfront.net/media/m1/v1/master.m3u8"


@override_settings(AWS_CLOUDFRONT_DOMAIN="")
def test_asset_url_is_unavailable_without_cloudfront_configuration():
    media = SimpleNamespace(pk="m1", active_asset_version_id="v1")
    asset = SimpleNamespace(version_id="v1", s3_key="private/key")
    assert cloudfront_asset_url(media, asset) is None
