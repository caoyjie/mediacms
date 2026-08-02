import json
from decimal import Decimal

import pytest

from files.models import Media, MediaIngestionJob, MediaJobAttempt
from files.services.media_probe import SourceFacts
from files.services.mediaconvert import (
    AmbiguousReconciliation,
    InvalidMediaConvertEvidence,
    MediaConvertGateway,
    ProviderSnapshot,
    build_job_request,
    match_reconciliation_job,
    submission_token,
)
from files.services.processing_storage import ObjectEvidence
from tests.users.factories import UserFactory


BUCKET = "mediacms-123456789012-us-east-1"
ROLE = "arn:aws:iam::123456789012:role/mediacms-dev-mediaconvert"
VIDEO_TEMPLATE = "mediacms-dev-video-hls-v1"
AUDIO_TEMPLATE = "mediacms-dev-audio-hls-v1"
TEMPLATE_VERSION = "h264-hls-qvbr-v1"


@pytest.fixture(autouse=True)
def aws_settings(settings):
    settings.AWS_MEDIA_BUCKET = BUCKET
    settings.AWS_REGION = "us-east-1"
    settings.AWS_MEDIACONVERT_ROLE_ARN = ROLE
    settings.AWS_MEDIACONVERT_VIDEO_TEMPLATE = VIDEO_TEMPLATE
    settings.AWS_MEDIACONVERT_AUDIO_TEMPLATE = AUDIO_TEMPLATE
    settings.AWS_MEDIACONVERT_TEMPLATE_VERSION = TEMPLATE_VERSION
    settings.AWS_ENVIRONMENT = "dev"


@pytest.fixture
def attempt(db):
    owner = UserFactory(is_staff=True, is_superuser=True)
    media = Media.objects.create(
        title="Private title must not leak",
        user=owner,
        media_type="video",
        storage_backend="aws",
    )
    job = MediaIngestionJob.objects.create(
        media=media,
        media_title_snapshot="Private snapshot must not leak",
        source_type="upload",
        source_metadata={
            "youtube_url": "https://youtube.example.invalid/watch?v=secret",
            "cookie": "secret-cookie",
        },
    )
    return MediaJobAttempt.objects.create(job=job, sequence=1)


def source_for(attempt, suffix="mp4"):
    return ObjectEvidence(
        key=f"originals/{attempt.job.media_id}/{attempt.id}/source.{suffix}",
        size=42,
        content_type="video/mp4" if suffix == "mp4" else "audio/mpeg",
        checksum="sha256:source-sha256",
    )


def destinations(request):
    values = []
    for group in request["Settings"]["OutputGroups"]:
        group_settings = group["OutputGroupSettings"]
        if group_settings["Type"] == "HLS_GROUP_SETTINGS":
            values.append(group_settings["HlsGroupSettings"]["Destination"])
        else:
            values.append(group_settings["FileGroupSettings"]["Destination"])
    return values


@pytest.mark.django_db
def test_video_request_filters_ladder_and_sets_deterministic_safe_contract(attempt):
    source = source_for(attempt)
    facts = SourceFacts("video", 20.0, 1280, 720, True)

    request = build_job_request(attempt, source, facts)

    assert request["Role"] == ROLE
    assert request["JobTemplate"] == VIDEO_TEMPLATE
    assert request["AccelerationSettings"] == {"Mode": "DISABLED"}
    assert request["BillingTagsSource"] == "JOB"
    assert request["ClientRequestToken"] == submission_token(
        attempt.id,
        TEMPLATE_VERSION,
        "sha256:source-sha256",
    )
    assert request["Settings"]["Inputs"] == [
        {
            "FileInput": f"s3://{BUCKET}/{source.key}",
            "AudioSelectors": {"Default Audio": {"DefaultSelection": "DEFAULT"}},
            "VideoSelector": {"Rotate": "AUTO"},
        }
    ]
    hls_outputs = request["Settings"]["OutputGroups"][0]["Outputs"]
    assert [output["NameModifier"] for output in hls_outputs] == [
        "_720p",
        "_480p",
        "_360p",
    ]
    assert [output["VideoDescription"]["Height"] for output in hls_outputs] == [
        720,
        480,
        360,
    ]
    candidate = f"s3://{BUCKET}/candidates/{attempt.job.media_id}/{attempt.id}"
    assert destinations(request) == [
        f"{candidate}/hls/master",
        f"{candidate}/images/poster",
    ]
    assert request["Tags"] == {
        "Project": "mediacms",
        "Environment": "dev",
        "MediaId": str(attempt.job.media_id),
        "JobId": str(attempt.job_id),
        "AttemptId": str(attempt.id),
        "SourceType": "upload",
        "TemplateVersion": TEMPLATE_VERSION,
    }
    assert request["UserMetadata"] == {
        "job_id": str(attempt.job_id),
        "attempt_id": str(attempt.id),
    }
    serialized = json.dumps(request)
    assert "Private title" not in serialized
    assert "youtube.example.invalid" not in serialized
    assert "secret-cookie" not in serialized


@pytest.mark.django_db
def test_silent_video_removes_audio_selectors_and_descriptions(attempt):
    request = build_job_request(
        attempt,
        source_for(attempt),
        SourceFacts("video", 20.0, 640, 360, False),
    )

    assert "AudioSelectors" not in request["Settings"]["Inputs"][0]
    outputs = request["Settings"]["OutputGroups"][0]["Outputs"]
    assert outputs and all("AudioDescriptions" not in output for output in outputs)


@pytest.mark.django_db
def test_audio_request_uses_audio_template_and_has_no_video_or_frame_capture(attempt):
    source = source_for(attempt, "mp3")
    request = build_job_request(
        attempt,
        source,
        SourceFacts("audio", 30.0, None, None, True),
    )

    assert request["JobTemplate"] == AUDIO_TEMPLATE
    assert request["Settings"]["Inputs"] == [
        {
            "FileInput": f"s3://{BUCKET}/{source.key}",
            "AudioSelectors": {"Default Audio": {"DefaultSelection": "DEFAULT"}},
        }
    ]
    assert len(request["Settings"]["OutputGroups"]) == 1
    outputs = request["Settings"]["OutputGroups"][0]["Outputs"]
    assert outputs[0]["NameModifier"] == "_audio"
    assert "VideoDescription" not in outputs[0]
    assert destinations(request) == [
        f"s3://{BUCKET}/candidates/{attempt.job.media_id}/{attempt.id}/hls/master"
    ]


@pytest.mark.django_db
def test_request_rejects_source_key_or_media_mismatch(attempt):
    foreign = ObjectEvidence(
        key="originals/other/attempt/source.mp4",
        size=42,
        content_type="video/mp4",
        checksum="sha256:sha256",
    )
    with pytest.raises(ValueError, match="attempt original"):
        build_job_request(
            attempt,
            foreign,
            SourceFacts("video", 20.0, 1920, 1080, True),
        )
    with pytest.raises(ValueError, match="media type"):
        build_job_request(
            attempt,
            source_for(attempt),
            SourceFacts("image", 20.0, None, None, False),
        )


def test_submission_token_is_stable_64_character_sha256():
    assert submission_token(
        "12345678-1234-1234-1234-123456789012",
        "h264-hls-qvbr-v1",
        "checksum-a",
    ) == "a8da99c0f25119b6d6ac22c06b1919d5a742289fc9139c7cf381ffeca3685bbc"
    assert submission_token(
        "12345678-1234-1234-1234-123456789012",
        "h264-hls-qvbr-v1",
        "checksum-b",
    ) != submission_token(
        "12345678-1234-1234-1234-123456789012",
        "h264-hls-qvbr-v1",
        "checksum-a",
    )


class RecordingMediaConvertClient:
    def __init__(self):
        self.calls = []
        self.create_response = {"Job": {"Id": "mc-job-1"}}
        self.get_response = None
        self.list_responses = []
        self.probe_response = {"ProbeResults": []}

    def probe(self, **kwargs):
        self.calls.append(("probe", kwargs))
        return self.probe_response

    def create_job(self, **kwargs):
        self.calls.append(("create_job", kwargs))
        return self.create_response

    def get_job(self, **kwargs):
        self.calls.append(("get_job", kwargs))
        return self.get_response

    def cancel_job(self, **kwargs):
        self.calls.append(("cancel_job", kwargs))
        return {}

    def list_jobs(self, **kwargs):
        self.calls.append(("list_jobs", kwargs))
        return self.list_responses.pop(0)


def provider_job(status, *, percent="missing"):
    job = {
        "Id": "mc-job-1",
        "Status": status,
        "CurrentPhase": "TRANSCODING",
        "Warnings": [{"Code": 230001, "Count": 1}],
        "OutputGroupDetails": [{"Type": "HLS_GROUP_DETAILS"}],
    }
    if percent != "missing":
        job["JobPercentComplete"] = percent
    return job


@pytest.mark.parametrize(
    ("status", "percent", "expected_percent"),
    [
        ("SUBMITTED", None, None),
        ("PROGRESSING", 42, 42.0),
        ("COMPLETE", 100, 100.0),
        ("CANCELED", "missing", None),
        ("ERROR", "missing", None),
    ],
)
def test_get_job_normalizes_all_provider_states(status, percent, expected_percent):
    client = RecordingMediaConvertClient()
    client.get_response = {"Job": provider_job(status, percent=percent)}

    snapshot = MediaConvertGateway(client=client).get_job("mc-job-1")

    assert snapshot == ProviderSnapshot(
        job_id="mc-job-1",
        status=status,
        phase="TRANSCODING",
        percent_complete=expected_percent,
        warnings=({"Code": 230001, "Count": 1},),
        output_group_details=({"Type": "HLS_GROUP_DETAILS"},),
    )
    assert client.calls == [("get_job", {"Id": "mc-job-1"})]


@pytest.mark.parametrize("percent", [-1, 101, "bad"])
def test_get_job_rejects_invalid_provider_percentage(percent):
    client = RecordingMediaConvertClient()
    client.get_response = {"Job": provider_job("PROGRESSING", percent=percent)}
    with pytest.raises(InvalidMediaConvertEvidence, match="percentage"):
        MediaConvertGateway(client=client).get_job("mc-job-1")


def test_gateway_forwards_probe_create_cancel_and_validates_create_job_id():
    client = RecordingMediaConvertClient()
    gateway = MediaConvertGateway(client=client)
    request = {"Role": ROLE, "Settings": {"Inputs": []}}

    assert gateway.probe("s3://bucket/original.mp4") == {"ProbeResults": []}
    assert gateway.create_job(request) == "mc-job-1"
    gateway.cancel_job("mc-job-1")
    assert client.calls == [
        ("probe", {"InputFiles": [{"FileUrl": "s3://bucket/original.mp4"}]}),
        ("create_job", request),
        ("cancel_job", {"Id": "mc-job-1"}),
    ]
    client.create_response = {"Job": {}}
    with pytest.raises(InvalidMediaConvertEvidence, match="Job ID"):
        gateway.create_job(request)


def reconciliation_job(request, job_id="mc-job-1"):
    return {
        "Id": job_id,
        "UserMetadata": dict(request["UserMetadata"]),
        "JobTemplate": request["JobTemplate"],
        "Settings": request["Settings"],
    }


@pytest.mark.django_db
def test_list_jobs_paginates_newest_and_reconciliation_matches_all_evidence(attempt):
    request = build_job_request(
        attempt,
        source_for(attempt),
        SourceFacts("video", 20.0, 640, 360, True),
    )
    match = reconciliation_job(request)
    client = RecordingMediaConvertClient()
    client.list_responses = [
        {"Jobs": [{"Id": "unrelated"}], "NextToken": "page-2"},
        {"Jobs": [match]},
    ]

    jobs = MediaConvertGateway(client=client).list_jobs()

    assert match_reconciliation_job(jobs, request) == match
    assert client.calls == [
        ("list_jobs", {"MaxResults": 20, "Order": "DESCENDING"}),
        (
            "list_jobs",
            {"MaxResults": 20, "Order": "DESCENDING", "NextToken": "page-2"},
        ),
    ]
    assert all(name != "create_job" for name, _ in client.calls)


@pytest.mark.django_db
def test_reconciliation_rejects_wrong_metadata_template_input_or_destination(attempt):
    request = build_job_request(
        attempt,
        source_for(attempt),
        SourceFacts("video", 20.0, 640, 360, True),
    )
    wrong_metadata = reconciliation_job(request, "metadata")
    wrong_metadata["UserMetadata"]["attempt_id"] = "other"
    wrong_template = reconciliation_job(request, "template")
    wrong_template["JobTemplate"] = AUDIO_TEMPLATE
    wrong_input = reconciliation_job(request, "input")
    wrong_input["Settings"] = json.loads(json.dumps(request["Settings"]))
    wrong_input["Settings"]["Inputs"][0]["FileInput"] = "s3://bucket/other.mp4"
    wrong_destination = reconciliation_job(request, "destination")
    wrong_destination["Settings"] = json.loads(json.dumps(request["Settings"]))
    wrong_destination["Settings"]["OutputGroups"][0]["OutputGroupSettings"][
        "HlsGroupSettings"
    ]["Destination"] = "s3://bucket/candidates/other/"
    missing_job_id = reconciliation_job(request, "temporary")
    del missing_job_id["Id"]

    assert (
        match_reconciliation_job(
            (
                wrong_metadata,
                wrong_template,
                wrong_input,
                wrong_destination,
                missing_job_id,
            ),
            request,
        )
        is None
    )


@pytest.mark.django_db
def test_reconciliation_rejects_multiple_exact_matches(attempt):
    request = build_job_request(
        attempt,
        source_for(attempt),
        SourceFacts("video", 20.0, 640, 360, True),
    )
    with pytest.raises(AmbiguousReconciliation):
        match_reconciliation_job(
            (
                reconciliation_job(request, "mc-job-1"),
                reconciliation_job(request, "mc-job-2"),
            ),
            request,
        )
