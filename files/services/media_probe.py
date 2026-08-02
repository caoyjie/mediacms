from dataclasses import dataclass
from math import isfinite
from urllib.parse import urlsplit

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings


class InvalidSourceMedia(ValueError):
    pass


class SourceProbeFailed(RuntimeError):
    pass


class SourceProbeRetryable(SourceProbeFailed):
    pass


@dataclass(frozen=True, slots=True)
class SourceFacts:
    media_type: str
    duration_seconds: float
    width: int | None
    height: int | None
    has_audio: bool


def _project_original_uri(source_s3_uri):
    if not isinstance(source_s3_uri, str):
        raise InvalidSourceMedia("Source must be a private project original.")
    parsed = urlsplit(source_s3_uri)
    parts = parsed.path.lstrip("/").split("/")
    if (
        parsed.scheme != "s3"
        or parsed.netloc != settings.AWS_MEDIA_BUCKET
        or parsed.query
        or parsed.fragment
        or len(parts) < 4
        or parts[0] != "originals"
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise InvalidSourceMedia("Source must be a private project original.")
    return source_s3_uri


def _positive_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value > 0
        and isfinite(value)
    )


def _even_dimension(value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 2:
        raise InvalidSourceMedia("Source video dimensions are unavailable.")
    return value - (value % 2)


def _normalize_probe_response(response):
    try:
        results = response["ProbeResults"]
        result = results[0]
        container = result["Container"]
        duration = container["Duration"]
        tracks = container["Tracks"]
    except (KeyError, IndexError, TypeError) as error:
        raise InvalidSourceMedia("MediaConvert returned incomplete source evidence.") from error
    if len(results) != 1 or not _positive_number(duration) or not isinstance(tracks, list):
        raise InvalidSourceMedia("MediaConvert returned invalid source evidence.")

    video_tracks = [track for track in tracks if track.get("TrackType") == "video"]
    audio_tracks = [track for track in tracks if track.get("TrackType") == "audio"]
    if video_tracks:
        try:
            properties = video_tracks[0]["VideoProperties"]
            width = _even_dimension(properties["Width"])
            height = _even_dimension(properties["Height"])
        except (KeyError, TypeError) as error:
            raise InvalidSourceMedia("Source video dimensions are unavailable.") from error
        return SourceFacts("video", float(duration), width, height, bool(audio_tracks))
    if audio_tracks:
        return SourceFacts("audio", float(duration), None, None, True)
    raise InvalidSourceMedia("Source has no supported video or audio track.")


def probe_source(source_s3_uri, gateway):
    exact_uri = _project_original_uri(source_s3_uri)
    try:
        response = gateway.probe(exact_uri)
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code", ""))
        if code in {"429", "Throttling", "ThrottlingException", "TooManyRequestsException"}:
            raise SourceProbeRetryable("Media source inspection is temporarily unavailable.") from error
        raise SourceProbeFailed("MediaConvert could not inspect the source.") from error
    except BotoCoreError as error:
        raise SourceProbeRetryable("Media source inspection is temporarily unavailable.") from error
    return _normalize_probe_response(response)


def allowed_video_heights(source_height):
    normalized_height = _even_dimension(source_height)
    return tuple(height for height in (360, 480, 720, 1080) if height <= normalized_height)
