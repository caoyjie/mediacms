from dataclasses import replace

import pytest

from files.models import (
    AttemptArtifact,
    Media,
    MediaIngestionJob,
    MediaJobAttempt,
    MediaJobCheckpoint,
)
from files.services.mediaconvert import ProviderSnapshot
from files.services.output_verification import (
    InvalidMediaConvertOutput,
    VerifiedOutput,
    VerifiedOutputSet,
    verify_mediaconvert_outputs,
)
from files.services.processing_storage import InvalidObjectEvidence, ObjectEvidence
from tests.users.factories import UserFactory


BUCKET = "mediacms-123456789012-us-east-1"


class MemoryStorage:
    def __init__(self, objects, manifests):
        self.objects = dict(objects)
        self.manifests = dict(manifests)
        self.list_calls = []
        self.head_calls = []
        self.text_calls = []

    def list_attempt_candidates(self, prefix):
        self.list_calls.append(prefix)
        return tuple(sorted(self.objects))

    def head_exact(self, key):
        self.head_calls.append(key)
        try:
            return self.objects[key]
        except KeyError as error:
            raise InvalidObjectEvidence("Object is missing.") from error

    def get_text(self, key):
        self.text_calls.append(key)
        try:
            body = self.manifests[key]
        except KeyError as error:
            raise InvalidObjectEvidence("Manifest is missing.") from error
        if isinstance(body, BaseException):
            raise body
        return body


def evidence(key, content_type, size=42):
    return ObjectEvidence(
        key=key,
        size=size,
        content_type=content_type,
        checksum=f"sha256:checksum:{key}",
    )


@pytest.fixture(autouse=True)
def output_settings(settings):
    settings.AWS_MEDIA_BUCKET = BUCKET


@pytest.fixture
def attempt_factory(db):
    owner = UserFactory(is_staff=True, is_superuser=True)

    def create(media_type="video"):
        media = Media.objects.create(
            title="Output verification",
            user=owner,
            media_type=media_type,
            storage_backend="aws",
            processing_status="processing",
            encoding_status="running",
        )
        job = MediaIngestionJob.objects.create(
            media=media,
            media_title_snapshot="Output verification",
            source_type="upload",
            status="running",
            stage="mediaconvert_complete",
        )
        attempt = MediaJobAttempt.objects.create(
            job=job,
            sequence=1,
            status="running",
            mediaconvert_job_id="mc-job-1",
            provider_status="COMPLETE",
        )
        MediaJobCheckpoint.objects.create(
            attempt=attempt,
            name="mediaconvert_complete",
            status="completed",
            evidence={"job_id": "mc-job-1", "provider_status": "COMPLETE"},
        )
        return attempt

    return create


def video_case(attempt):
    prefix = f"candidates/{attempt.job.media_id}/{attempt.id}/"
    master = f"{prefix}hls/master.m3u8"
    variant = f"{prefix}hls/master_720p.m3u8"
    segment = f"{prefix}hls/master_720p00001.ts"
    poster = f"{prefix}images/poster.0000000.jpg"
    extra = f"{prefix}images/poster.0000001.jpg"
    objects = {
        master: evidence(master, "application/vnd.apple.mpegurl"),
        variant: evidence(variant, "application/vnd.apple.mpegurl"),
        segment: evidence(segment, "video/mp2t"),
        poster: evidence(poster, "image/jpeg"),
        extra: evidence(extra, "image/jpeg"),
    }
    manifests = {
        master: "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=4000000\nmaster_720p.m3u8\n",
        variant: "#EXTM3U\n#EXTINF:4.0,\nmaster_720p00001.ts\n#EXT-X-ENDLIST\n",
    }
    snapshot = ProviderSnapshot(
        job_id="mc-job-1",
        status="COMPLETE",
        phase=None,
        percent_complete=100,
        warnings=(),
        output_group_details=(
            {
                "Type": "HLS_GROUP",
                "PlaylistFilePaths": [f"s3://{BUCKET}/{master}"],
                "OutputDetails": [
                    {"OutputFilePaths": [f"s3://{BUCKET}/{variant}"]}
                ],
            },
            {
                "Type": "FILE_GROUP",
                "OutputDetails": [
                    {"OutputFilePaths": [f"s3://{BUCKET}/{poster}"]}
                ],
            },
        ),
    )
    return prefix, objects, manifests, snapshot, (master, variant, segment, poster, extra)


@pytest.mark.django_db
def test_video_verification_returns_exact_closure_and_ledgers_all_inventory(
    attempt_factory,
):
    attempt = attempt_factory()
    prefix, objects, manifests, snapshot, keys = video_case(attempt)
    storage = MemoryStorage(objects, manifests)

    result = verify_mediaconvert_outputs(attempt.id, snapshot, storage)

    assert result == VerifiedOutputSet(
        manifest_key=keys[0],
        outputs=(
            VerifiedOutput("hls_master", objects[keys[0]]),
            VerifiedOutput("hls_variant", objects[keys[1]]),
            VerifiedOutput("hls_segment", objects[keys[2]]),
            VerifiedOutput("poster", objects[keys[3]]),
        ),
    )
    assert storage.list_calls == [prefix]
    assert set(storage.head_calls) == set(keys)
    assert set(
        AttemptArtifact.objects.filter(attempt=attempt).values_list(
            "s3_key",
            flat=True,
        )
    ) == set(keys)
    assert not any(output.evidence.key == keys[4] for output in result.outputs)


@pytest.mark.django_db
def test_audio_requires_master_and_variant_but_not_image(attempt_factory):
    attempt = attempt_factory("audio")
    prefix = f"candidates/{attempt.job.media_id}/{attempt.id}/"
    master = f"{prefix}hls/master.m3u8"
    variant = f"{prefix}hls/master_audio.m3u8"
    segment = f"{prefix}hls/master_audio00001.aac"
    objects = {
        master: evidence(master, "application/x-mpegurl"),
        variant: evidence(variant, "application/x-mpegurl"),
        segment: evidence(segment, "audio/aac"),
    }
    manifests = {
        master: "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=128000\nmaster_audio.m3u8\n",
        variant: "#EXTM3U\n#EXTINF:4.0,\nmaster_audio00001.aac\n",
    }
    snapshot = ProviderSnapshot(
        "mc-job-1",
        "COMPLETE",
        None,
        100,
        (),
        (
            {
                "Type": "HLS_GROUP",
                "PlaylistFilePaths": [f"s3://{BUCKET}/{master}"],
                "OutputDetails": [
                    {"OutputFilePaths": [f"s3://{BUCKET}/{variant}"]}
                ],
            },
        ),
    )

    result = verify_mediaconvert_outputs(
        attempt.id,
        snapshot,
        MemoryStorage(objects, manifests),
    )

    assert result.manifest_key == master
    assert [output.kind for output in result.outputs] == [
        "hls_master",
        "hls_variant",
        "hls_segment",
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path_mutation",
    (
        "foreign_bucket",
        "wrong_attempt",
        "traversal",
        "encoded_traversal",
        "https",
        "query",
        "fragment",
        "backslash",
    ),
)
def test_provider_paths_reject_foreign_prefix_traversal_and_non_s3_uri(
    attempt_factory,
    path_mutation,
):
    attempt = attempt_factory()
    prefix, objects, manifests, snapshot, keys = video_case(attempt)
    master = keys[0]
    mutations = {
        "foreign_bucket": f"s3://foreign-bucket/{master}",
        "wrong_attempt": f"s3://{BUCKET}/candidates/{attempt.job.media_id}/wrong/master.m3u8",
        "traversal": f"s3://{BUCKET}/{prefix}hls/../master.m3u8",
        "encoded_traversal": f"s3://{BUCKET}/{prefix}hls/%2e%2e/master.m3u8",
        "https": f"https://{BUCKET}.s3.amazonaws.com/{master}",
        "query": f"s3://{BUCKET}/{master}?version=1",
        "fragment": f"s3://{BUCKET}/{master}#fragment",
        "backslash": f"s3://{BUCKET}/{prefix}hls\\master.m3u8",
    }
    details = list(snapshot.output_group_details)
    details[0] = dict(details[0], PlaylistFilePaths=[mutations[path_mutation]])

    with pytest.raises(InvalidMediaConvertOutput, match="path"):
        verify_mediaconvert_outputs(
            attempt.id,
            replace(snapshot, output_group_details=tuple(details)),
            MemoryStorage(objects, manifests),
        )


@pytest.mark.django_db
def test_duplicate_master_is_rejected(attempt_factory):
    attempt = attempt_factory()
    _, objects, manifests, snapshot, keys = video_case(attempt)
    details = list(snapshot.output_group_details)
    duplicate = dict(details[0], PlaylistFilePaths=[f"s3://{BUCKET}/{keys[0]}"])

    with pytest.raises(InvalidMediaConvertOutput, match="unique master"):
        verify_mediaconvert_outputs(
            attempt.id,
            replace(snapshot, output_group_details=(details[0], duplicate, details[1])),
            MemoryStorage(objects, manifests),
        )


@pytest.mark.django_db
def test_unknown_provider_output_group_type_is_rejected(attempt_factory):
    attempt = attempt_factory()
    _, objects, manifests, snapshot, _ = video_case(attempt)
    unknown = {"Type": "DASH_ISO_GROUP", "OutputDetails": []}

    with pytest.raises(InvalidMediaConvertOutput, match="group type"):
        verify_mediaconvert_outputs(
            attempt.id,
            replace(snapshot, output_group_details=snapshot.output_group_details + (unknown,)),
            MemoryStorage(objects, manifests),
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "unsafe_body",
    (
        "#EXTM3U\nhttps://cdn.example/segment.ts\n",
        "#EXTM3U\n../segment.ts\n",
        "#EXTM3U\nsegment.ts?token=secret\n",
        "#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"key.bin\"\nsegment.ts\n",
    ),
)
def test_manifest_rejects_external_traversal_query_and_encryption(
    attempt_factory,
    unsafe_body,
):
    attempt = attempt_factory()
    _, objects, manifests, snapshot, keys = video_case(attempt)
    manifests[keys[1]] = unsafe_body

    with pytest.raises(InvalidMediaConvertOutput, match="manifest"):
        verify_mediaconvert_outputs(
            attempt.id,
            snapshot,
            MemoryStorage(objects, manifests),
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "manifest_error",
    (
        InvalidObjectEvidence("Object exceeds the manifest size limit."),
        InvalidObjectEvidence("Object is not valid UTF-8 text."),
    ),
)
def test_oversized_and_non_utf8_manifest_evidence_is_rejected(
    attempt_factory,
    manifest_error,
):
    attempt = attempt_factory()
    _, objects, manifests, snapshot, keys = video_case(attempt)
    manifests[keys[0]] = manifest_error

    with pytest.raises(InvalidObjectEvidence):
        verify_mediaconvert_outputs(
            attempt.id,
            snapshot,
            MemoryStorage(objects, manifests),
        )


@pytest.mark.django_db
def test_missing_dependency_and_unexpected_provider_variant_are_rejected(
    attempt_factory,
):
    attempt = attempt_factory()
    _, objects, manifests, snapshot, keys = video_case(attempt)
    objects.pop(keys[2])
    with pytest.raises(InvalidMediaConvertOutput, match="missing"):
        verify_mediaconvert_outputs(
            attempt.id,
            snapshot,
            MemoryStorage(objects, manifests),
        )

    objects[keys[2]] = evidence(keys[2], "video/mp2t")
    unreferenced = keys[1].replace("720p", "480p")
    details = list(snapshot.output_group_details)
    details[0] = dict(
        details[0],
        OutputDetails=[{"OutputFilePaths": [f"s3://{BUCKET}/{unreferenced}"]}],
    )
    objects[unreferenced] = evidence(unreferenced, "application/x-mpegurl")
    manifests[unreferenced] = "#EXTM3U\n#EXTINF:4.0,\nmaster_720p00001.ts\n"
    with pytest.raises(InvalidMediaConvertOutput, match="not referenced"):
        verify_mediaconvert_outputs(
            attempt.id,
            replace(snapshot, output_group_details=tuple(details)),
            MemoryStorage(objects, manifests),
        )


@pytest.mark.django_db
def test_master_rejects_variant_not_declared_by_provider_output_details(attempt_factory):
    attempt = attempt_factory()
    _, objects, manifests, snapshot, keys = video_case(attempt)
    undeclared = keys[1].replace("720p", "480p")
    undeclared_segment = keys[2].replace("720p", "480p")
    objects[undeclared] = evidence(undeclared, "application/x-mpegurl")
    objects[undeclared_segment] = evidence(undeclared_segment, "video/mp2t")
    manifests[keys[0]] = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=4000000\nmaster_720p.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=1000000\nmaster_480p.m3u8\n"
    )
    manifests[undeclared] = "#EXTM3U\n#EXTINF:4.0,\nmaster_480p00001.ts\n"

    with pytest.raises(InvalidMediaConvertOutput, match="not declared"):
        verify_mediaconvert_outputs(
            attempt.id,
            snapshot,
            MemoryStorage(objects, manifests),
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("remove_image", "image"),
        ("zero_segment", "nonzero"),
        ("wrong_type", "content type"),
    ),
)
def test_video_requires_image_and_valid_nonzero_typed_closure_objects(
    attempt_factory,
    mutation,
    message,
):
    attempt = attempt_factory()
    _, objects, manifests, snapshot, keys = video_case(attempt)
    if mutation == "remove_image":
        snapshot = replace(snapshot, output_group_details=snapshot.output_group_details[:1])
    elif mutation == "zero_segment":
        objects[keys[2]] = evidence(keys[2], "video/mp2t", size=0)
    else:
        objects[keys[2]] = evidence(keys[2], "text/plain")

    with pytest.raises(InvalidMediaConvertOutput, match=message):
        verify_mediaconvert_outputs(
            attempt.id,
            snapshot,
            MemoryStorage(objects, manifests),
        )
    if mutation == "remove_image":
        assert set(
            AttemptArtifact.objects.filter(attempt=attempt).values_list(
                "s3_key",
                flat=True,
            )
        ) == set(objects)
