from uuid import uuid4

import pytest

from files.models import Category, Media, MediaAsset, MediaAssetVersion, MediaIngestionJob, Tag
from files.services.media_state import (
    InvalidAssetActivation,
    InvalidMediaTransition,
    MediaRevisionConflict,
    activate_asset_version,
    request_media_deletion,
    transition_media,
    update_media_metadata,
)
from tests.users.factories import UserFactory


def create_aws_media(title: str, **overrides) -> Media:
    values = {
        "title": title,
        "user": UserFactory(username=f"aws-{uuid4().hex}"),
        "friendly_token": f"aws-{uuid4().hex}",
        "storage_backend": "aws",
        "media_file": "aws-source/pending-upload",
    }
    values.update(overrides)
    media = Media(**values)
    Media.objects.bulk_create([media])
    return media


def create_complete_candidate(media: Media, key: str) -> MediaAssetVersion:
    version = MediaAssetVersion.objects.create(media=media, status="candidate", manifest_key=key)
    MediaAsset.objects.create(
        version=version,
        kind="hls_master",
        s3_key=key,
        checksum="sha256:manifest",
    )
    return version


@pytest.mark.django_db(transaction=True)
def test_transition_projects_legacy_encoding_status():
    media = create_aws_media("Transition")

    transition_media(media.id, "queued")
    transition_media(media.id, "processing")

    media.refresh_from_db()
    assert (media.processing_status, media.encoding_status) == ("processing", "running")


@pytest.mark.django_db(transaction=True)
def test_illegal_transition_preserves_current_state():
    media = create_aws_media("Illegal transition")

    with pytest.raises(InvalidMediaTransition):
        transition_media(media.id, "ready")

    media.refresh_from_db()
    assert (media.processing_status, media.encoding_status) == ("draft", "pending")


@pytest.mark.django_db(transaction=True)
def test_activation_switches_complete_version_in_one_transaction():
    media = create_aws_media("Activation", processing_status="processing", encoding_status="running")
    candidate = create_complete_candidate(media, "candidates/a/master.m3u8")

    activate_asset_version(media.id, candidate.id)

    media.refresh_from_db()
    candidate.refresh_from_db()
    assert media.active_asset_version_id == candidate.id
    assert media.processing_status == "ready"
    assert media.encoding_status == "success"
    assert candidate.status == "active"
    assert candidate.activated_at is not None


@pytest.mark.django_db(transaction=True)
def test_activation_retires_previous_version():
    media = create_aws_media("Replacement", processing_status="processing", encoding_status="running")
    current = create_complete_candidate(media, "active/current/master.m3u8")
    current.status = "active"
    current.save(update_fields=["status"])
    Media.objects.filter(pk=media.pk).update(active_asset_version=current)
    candidate = create_complete_candidate(media, "candidates/replacement/master.m3u8")

    activate_asset_version(media.id, candidate.id)

    current.refresh_from_db()
    media.refresh_from_db()
    assert current.status == "retired"
    assert media.active_asset_version_id == candidate.id


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("invalid_kind", ["missing_manifest", "cross_media", "retired"])
def test_invalid_candidate_never_replaces_existing_active_version(invalid_kind):
    media = create_aws_media("Protected active", processing_status="processing", encoding_status="running")
    active = create_complete_candidate(media, "active/protected/master.m3u8")
    active.status = "active"
    active.save(update_fields=["status"])
    Media.objects.filter(pk=media.pk).update(active_asset_version=active)

    if invalid_kind == "missing_manifest":
        candidate = MediaAssetVersion.objects.create(
            media=media,
            status="candidate",
            manifest_key="candidates/missing/master.m3u8",
        )
    elif invalid_kind == "cross_media":
        candidate = create_complete_candidate(create_aws_media("Other"), "candidates/other/master.m3u8")
    else:
        candidate = create_complete_candidate(media, "retired/master.m3u8")
        candidate.status = "retired"
        candidate.save(update_fields=["status"])

    with pytest.raises(InvalidAssetActivation):
        activate_asset_version(media.id, candidate.id)

    media.refresh_from_db()
    active.refresh_from_db()
    assert media.active_asset_version_id == active.id
    assert active.status == "active"
    assert media.processing_status == "processing"


@pytest.mark.django_db(transaction=True)
def test_admin_metadata_update_increments_revision_and_owns_sources():
    media = create_aws_media("Before")
    category = Category.objects.create(title="Architecture")
    tag = Tag.objects.create(title="AWS")

    updated = update_media_metadata(
        media.id,
        expected_revision=1,
        changes={
            "title": "After",
            "description": "Managed remotely",
            "category_ids": [category.id],
            "tag_ids": [tag.id],
        },
        source="admin",
    )

    assert updated.revision == 2
    assert updated.title == "After"
    assert set(updated.category.values_list("id", flat=True)) == {category.id}
    assert set(updated.tags.values_list("id", flat=True)) == {tag.id}
    assert updated.metadata_sources == {
        "category_ids": "admin",
        "description": "admin",
        "tag_ids": "admin",
        "title": "admin",
    }


@pytest.mark.django_db(transaction=True)
def test_stale_metadata_revision_returns_current_values_without_writing():
    media = create_aws_media("Current", revision=3, metadata_sources={"title": "admin"})

    with pytest.raises(MediaRevisionConflict) as captured:
        update_media_metadata(
            media.id,
            expected_revision=2,
            changes={"title": "Stale"},
            source="admin",
        )

    media.refresh_from_db()
    assert captured.value.current_revision == 3
    assert captured.value.current_values["title"] == "Current"
    assert media.title == "Current"


@pytest.mark.django_db(transaction=True)
def test_automatic_metadata_never_overwrites_admin_owned_value():
    media = create_aws_media("Admin title", metadata_sources={"title": "admin"})

    updated = update_media_metadata(
        media.id,
        expected_revision=1,
        changes={"title": "YouTube title", "description": "Discovered"},
        source="youtube",
    )

    assert updated.title == "Admin title"
    assert updated.description == "Discovered"
    assert updated.revision == 2
    assert updated.metadata_sources == {"title": "admin", "description": "youtube"}


@pytest.mark.django_db(transaction=True)
def test_visibility_update_preserves_existing_listability_contract():
    media = create_aws_media(
        "Visibility",
        state="private",
        encoding_status="success",
        is_reviewed=True,
        listable=False,
    )

    updated = update_media_metadata(
        media.id,
        expected_revision=1,
        changes={"state": "public"},
        source="admin",
    )

    assert updated.state == "public"
    assert updated.listable is True
    assert updated.revision == 2


@pytest.mark.django_db(transaction=True)
def test_reordered_relationship_ids_do_not_increment_revision():
    media = create_aws_media("Sets", revision=4, metadata_sources={"tag_ids": "admin"})
    first = Tag.objects.create(title="First")
    second = Tag.objects.create(title="Second")
    media.tags.set([first, second])

    updated = update_media_metadata(
        media.id,
        expected_revision=4,
        changes={"tag_ids": [second.id, first.id]},
        source="admin",
    )

    assert updated.revision == 4


@pytest.mark.django_db(transaction=True)
def test_deletion_request_hides_media_and_is_idempotent_at_current_revision():
    media = create_aws_media("Delete", listable=True)
    job = MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot=media.title,
        source_type="upload",
    )

    pending = request_media_deletion(media.id, expected_revision=1)
    repeated = request_media_deletion(media.id, expected_revision=2)

    assert pending.deletion_status == "pending"
    assert pending.revision == 2
    assert repeated.deletion_status == "pending"
    assert repeated.revision == 2
    assert repeated.listable is False
    assert MediaIngestionJob.objects.filter(pk=job.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_cleanup_failure_does_not_change_ready_media():
    media = create_aws_media("Ready", processing_status="processing", encoding_status="running")
    version = create_complete_candidate(media, "candidates/ready/master.m3u8")
    activate_asset_version(media.id, version.id)
    job = MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot=media.title,
        source_type="upload",
        status="completed",
        cleanup_status="failed",
    )

    media.refresh_from_db()
    assert media.processing_status == "ready"
    assert media.active_asset_version_id == version.id
    assert job.cleanup_status == "failed"
