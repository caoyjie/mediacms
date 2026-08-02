import pytest
from botocore.exceptions import ClientError

from files.services.media_probe import (
    InvalidSourceMedia,
    SourceFacts,
    SourceProbeFailed,
    SourceProbeRetryable,
    allowed_video_heights,
    probe_source,
)


BUCKET = "mediacms-123456789012-us-east-1"
SOURCE_URI = f"s3://{BUCKET}/originals/1/attempt/source.mp4"


class ProbeGateway:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def probe(self, source_s3_uri):
        self.calls.append(source_s3_uri)
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def aws_bucket(settings):
    settings.AWS_MEDIA_BUCKET = BUCKET


def probe_result(*tracks, duration=20.25, container_format="mp4"):
    return {
        "ProbeResults": [
            {
                "Container": {
                    "Format": container_format,
                    "Duration": duration,
                    "Tracks": list(tracks),
                }
            }
        ]
    }


def video_track(width=1920, height=1080):
    return {
        "TrackType": "video",
        "Codec": "AVC",
        "VideoProperties": {"Width": width, "Height": height},
    }


def audio_track():
    return {
        "TrackType": "audio",
        "Codec": "AAC",
        "AudioProperties": {"Channels": 2, "SampleRate": 48_000},
    }


def test_probe_normalizes_video_tracks_and_audio_presence():
    gateway = ProbeGateway(probe_result(video_track(), audio_track()))

    facts = probe_source(SOURCE_URI, gateway)

    assert facts == SourceFacts(
        media_type="video",
        duration_seconds=20.25,
        width=1920,
        height=1080,
        has_audio=True,
    )
    assert gateway.calls == [SOURCE_URI]


def test_probe_normalizes_audio_only_source_without_fake_dimensions():
    gateway = ProbeGateway(probe_result(audio_track(), duration=30.5, container_format="wave"))

    assert probe_source(SOURCE_URI, gateway) == SourceFacts(
        media_type="audio",
        duration_seconds=30.5,
        width=None,
        height=None,
        has_audio=True,
    )


def test_probe_rounds_odd_video_dimensions_down_to_even_values():
    gateway = ProbeGateway(probe_result(video_track(width=1921, height=1081)))

    facts = probe_source(SOURCE_URI, gateway)

    assert (facts.width, facts.height) == (1920, 1080)


@pytest.mark.parametrize(
    ("source_height", "expected"),
    [
        (2160, (360, 480, 720, 1080)),
        (1080, (360, 480, 720, 1080)),
        (1079, (360, 480, 720)),
        (720, (360, 480, 720)),
        (481, (360, 480)),
        (359, ()),
    ],
)
def test_video_ladder_never_upscales(source_height, expected):
    assert allowed_video_heights(source_height) == expected


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "https://signed.example.invalid/originals/1/attempt/source.mp4?secret=value",
        "file:///tmp/source.mp4",
        "s3://foreign-bucket/originals/1/attempt/source.mp4",
        f"s3://{BUCKET}/uploads/1/session/source.mp4",
        f"s3://{BUCKET}/candidates/1/attempt/master.m3u8",
        f"s3://{BUCKET}/originals/1/../secret.mp4",
    ],
)
def test_probe_rejects_non_project_originals_before_gateway_call(uri):
    gateway = ProbeGateway({})

    with pytest.raises(InvalidSourceMedia, match="project original") as captured:
        probe_source(uri, gateway)

    assert gateway.calls == []
    if uri:
        assert uri not in str(captured.value)


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"ProbeResults": []},
        {"ProbeResults": [{"Container": {}}]},
        probe_result(duration=0, *[audio_track()]),
        probe_result({"TrackType": "data", "DataProperties": {}}),
        probe_result(
            {
                "TrackType": "video",
                "VideoProperties": {"Width": 1920},
            }
        ),
    ],
)
def test_probe_rejects_missing_container_duration_tracks_or_dimensions(response):
    with pytest.raises(InvalidSourceMedia):
        probe_source(SOURCE_URI, ProbeGateway(response))


def test_probe_maps_throttling_to_retryable_safe_error_without_uri():
    error = ClientError(
        {
            "Error": {
                "Code": "TooManyRequestsException",
                "Message": f"rate limited while probing {SOURCE_URI}",
            }
        },
        "Probe",
    )

    with pytest.raises(SourceProbeRetryable) as captured:
        probe_source(SOURCE_URI, ProbeGateway(error=error))

    assert SOURCE_URI not in str(captured.value)


def test_probe_maps_unsupported_input_to_non_retryable_safe_error_without_uri():
    error = ClientError(
        {
            "Error": {
                "Code": "BadRequestException",
                "Message": f"unsupported input {SOURCE_URI}",
            }
        },
        "Probe",
    )

    with pytest.raises(SourceProbeFailed) as captured:
        probe_source(SOURCE_URI, ProbeGateway(error=error))

    assert not isinstance(captured.value, SourceProbeRetryable)
    assert SOURCE_URI not in str(captured.value)
