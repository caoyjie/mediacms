import pytest

from files import helpers
from files.models import Media
from files.models.domain import MediaProcessingStatus, encoding_status_for
from tests.users.factories import UserFactory


@pytest.mark.django_db
def test_aws_media_defaults_to_draft_without_reusing_visibility_state():
    media = Media(
        title="AWS draft",
        user=UserFactory(),
        storage_backend="aws",
        media_file="aws-source/pending-upload",
    )
    Media.objects.bulk_create([media])

    assert media.state == helpers.get_portal_workflow()
    assert media.processing_status == MediaProcessingStatus.DRAFT
    assert media.encoding_status == "pending"
    assert media.revision == 1
    assert media.metadata_sources == {}
    assert media.deletion_status == "none"


@pytest.mark.parametrize(
    ("processing", "encoding"),
    [
        ("draft", "pending"),
        ("queued", "pending"),
        ("processing", "running"),
        ("ready", "success"),
        ("failed", "fail"),
    ],
)
def test_encoding_projection(processing, encoding):
    assert encoding_status_for(processing) == encoding


def test_encoding_projection_rejects_unknown_status():
    with pytest.raises(ValueError, match="Unknown media processing status"):
        encoding_status_for("completed")
