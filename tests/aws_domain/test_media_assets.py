import pytest
from django.db import IntegrityError, transaction

from files.models import Media, MediaAsset, MediaAssetVersion
from tests.users.factories import UserFactory


def create_aws_media(title: str) -> Media:
    media = Media(
        title=title,
        user=UserFactory(),
        storage_backend="aws",
        media_file="aws-source/pending-upload",
    )
    Media.objects.bulk_create([media])
    return media


@pytest.mark.django_db
def test_asset_key_is_unique_inside_a_version():
    media = create_aws_media("Asset test")
    version = MediaAssetVersion.objects.create(
        media=media,
        status="candidate",
        manifest_key="media/1/candidate/master.m3u8",
    )
    MediaAsset.objects.create(
        version=version,
        kind="hls_master",
        s3_key=version.manifest_key,
        checksum="sha256:one",
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        MediaAsset.objects.create(
            version=version,
            kind="hls_master",
            s3_key=version.manifest_key,
            checksum="sha256:two",
        )


@pytest.mark.django_db
def test_active_pointer_references_a_version_without_changing_processing_state():
    media = create_aws_media("Pointer test")
    version = MediaAssetVersion.objects.create(
        media=media,
        status="candidate",
        manifest_key="media/2/candidate/master.m3u8",
    )

    media.active_asset_version = version
    media.save(update_fields=["active_asset_version"])

    media.refresh_from_db()
    assert media.active_asset_version_id == version.id
    assert media.processing_status == "draft"


@pytest.mark.django_db
def test_same_s3_key_can_exist_in_distinct_versions():
    media = create_aws_media("Replacement test")
    first = MediaAssetVersion.objects.create(
        media=media,
        status="retired",
        manifest_key="media/3/master.m3u8",
    )
    second = MediaAssetVersion.objects.create(
        media=media,
        status="candidate",
        manifest_key="media/3/master.m3u8",
    )

    MediaAsset.objects.create(version=first, kind="hls_master", s3_key=first.manifest_key)
    MediaAsset.objects.create(version=second, kind="hls_master", s3_key=second.manifest_key)

    assert MediaAsset.objects.filter(s3_key="media/3/master.m3u8").count() == 2
