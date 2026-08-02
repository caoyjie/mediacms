from django.conf import settings


def cloudfront_asset_url(media, asset, *, filename=None):
    domain = getattr(settings, "AWS_CLOUDFRONT_DOMAIN", "").strip()
    if not domain or not media.active_asset_version_id or asset.version_id != media.active_asset_version_id:
        return None
    name = filename or asset.s3_key.rsplit("/", 1)[-1]
    return f"https://{domain}/media/{media.pk}/{asset.version_id}/{name}"


def active_asset_url(media, kind, *, filename=None):
    version = getattr(media, "active_asset_version", None)
    if version is None:
        return None
    asset = version.assets.filter(kind=kind).order_by("s3_key").first()
    return cloudfront_asset_url(media, asset, filename=filename) if asset else None
