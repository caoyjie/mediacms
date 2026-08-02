from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256

from django.conf import settings

from files.services.media_probe import SourceFacts, allowed_video_heights


class InvalidMediaConvertEvidence(RuntimeError):
    pass


class AmbiguousReconciliation(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderSnapshot:
    job_id: str
    status: str
    phase: str | None
    percent_complete: float | None
    warnings: tuple[dict, ...]
    output_group_details: tuple[dict, ...]


_VIDEO_RENDITIONS = {
    1080: (1920, 8, 6_000_000),
    720: (1280, 8, 4_000_000),
    480: (854, 7, 1_000_000),
    360: (640, 7, 700_000),
}
_PROVIDER_STATUSES = {"SUBMITTED", "PROGRESSING", "COMPLETE", "CANCELED", "ERROR"}


def submission_token(attempt_id, template_version, input_checksum):
    material = f"{attempt_id}{template_version}{input_checksum}".encode("utf-8")
    return sha256(material).hexdigest()


def _audio_description():
    return {
        "AudioSourceName": "Default Audio",
        "CodecSettings": {
            "Codec": "AAC",
            "AacSettings": {
                "Bitrate": 128000,
                "CodingMode": "CODING_MODE_2_0",
                "CodecProfile": "LC",
                "SampleRate": 48000,
                "Specification": "MPEG4",
            },
        },
    }


def _video_output(height, has_audio):
    width, quality, max_bitrate = _VIDEO_RENDITIONS[height]
    output = {
        "NameModifier": f"_{height}p",
        "ContainerSettings": {"Container": "M3U8", "M3u8Settings": {}},
        "VideoDescription": {
            "Width": width,
            "Height": height,
            "CodecSettings": {
                "Codec": "H_264",
                "H264Settings": {
                    "FramerateControl": "INITIALIZE_FROM_SOURCE",
                    "GopSize": 2.0,
                    "GopSizeUnits": "SECONDS",
                    "ParControl": "INITIALIZE_FROM_SOURCE",
                    "QualityTuningLevel": "SINGLE_PASS_HQ",
                    "RateControlMode": "QVBR",
                    "MaxBitrate": max_bitrate,
                    "QvbrSettings": {"QvbrQualityLevel": quality},
                },
            },
        },
    }
    if has_audio:
        output["AudioDescriptions"] = [_audio_description()]
    return output


def _poster_output(facts):
    return {
        "NameModifier": "_poster",
        "ContainerSettings": {"Container": "RAW"},
        "VideoDescription": {
            "Width": min(facts.width, 1280),
            "Height": min(facts.height, 720),
            "CodecSettings": {
                "Codec": "FRAME_CAPTURE",
                "FrameCaptureSettings": {
                    "FramerateNumerator": 1,
                    "FramerateDenominator": 1,
                    "MaxCaptures": 1,
                    "Quality": 80,
                },
            },
        },
    }


def _video_settings(source_uri, candidate_uri, facts):
    heights = tuple(reversed(allowed_video_heights(facts.height)))
    if not heights:
        raise ValueError("Source video is below the minimum output height.")
    input_settings = {
        "FileInput": source_uri,
        "VideoSelector": {"Rotate": "AUTO"},
    }
    if facts.has_audio:
        input_settings["AudioSelectors"] = {
            "Default Audio": {"DefaultSelection": "DEFAULT"}
        }
    return {
        "Inputs": [input_settings],
        "OutputGroups": [
            {
                "Name": "Apple HLS",
                "OutputGroupSettings": {
                    "Type": "HLS_GROUP_SETTINGS",
                    "HlsGroupSettings": {
                        "Destination": f"{candidate_uri}/hls/master",
                        "MinSegmentLength": 0,
                        "SegmentLength": 4,
                    },
                },
                "Outputs": [_video_output(height, facts.has_audio) for height in heights],
            },
            {
                "Name": "First valid video frame",
                "OutputGroupSettings": {
                    "Type": "FILE_GROUP_SETTINGS",
                    "FileGroupSettings": {
                        "Destination": f"{candidate_uri}/images/poster"
                    },
                },
                "Outputs": [_poster_output(facts)],
            },
        ],
    }


def _audio_settings(source_uri, candidate_uri):
    return {
        "Inputs": [
            {
                "FileInput": source_uri,
                "AudioSelectors": {
                    "Default Audio": {"DefaultSelection": "DEFAULT"}
                },
            }
        ],
        "OutputGroups": [
            {
                "Name": "Apple HLS Audio",
                "OutputGroupSettings": {
                    "Type": "HLS_GROUP_SETTINGS",
                    "HlsGroupSettings": {
                        "Destination": f"{candidate_uri}/hls/master",
                        "MinSegmentLength": 0,
                        "SegmentLength": 4,
                    },
                },
                "Outputs": [
                    {
                        "NameModifier": "_audio",
                        "ContainerSettings": {
                            "Container": "M3U8",
                            "M3u8Settings": {},
                        },
                        "AudioDescriptions": [_audio_description()],
                    }
                ],
            }
        ],
    }


def _configured(name):
    value = getattr(settings, name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is not configured")
    return value


def build_job_request(attempt, source, facts):
    media_id = attempt.job.media_id
    expected_source_prefix = f"originals/{media_id}/{attempt.id}/"
    if not source.key.startswith(expected_source_prefix):
        raise ValueError("Source is not the attempt original.")
    if facts.media_type not in {"video", "audio"}:
        raise ValueError("Unsupported media type.")
    if facts.media_type == "audio" and not facts.has_audio:
        raise ValueError("Audio source has no audio track.")

    bucket = _configured("AWS_MEDIA_BUCKET")
    role = _configured("AWS_MEDIACONVERT_ROLE_ARN")
    environment = _configured("AWS_ENVIRONMENT")
    template_version = _configured("AWS_MEDIACONVERT_TEMPLATE_VERSION")
    template = _configured(
        "AWS_MEDIACONVERT_VIDEO_TEMPLATE"
        if facts.media_type == "video"
        else "AWS_MEDIACONVERT_AUDIO_TEMPLATE"
    )
    source_uri = f"s3://{bucket}/{source.key}"
    candidate_uri = f"s3://{bucket}/candidates/{media_id}/{attempt.id}"
    job_settings = (
        _video_settings(source_uri, candidate_uri, facts)
        if facts.media_type == "video"
        else _audio_settings(source_uri, candidate_uri)
    )
    return {
        "Role": role,
        "JobTemplate": template,
        "Settings": job_settings,
        "AccelerationSettings": {"Mode": "DISABLED"},
        "BillingTagsSource": "JOB",
        "ClientRequestToken": submission_token(
            attempt.id,
            template_version,
            source.checksum_sha256,
        ),
        "Tags": {
            "Project": "mediacms",
            "Environment": environment,
            "MediaId": str(media_id),
            "JobId": str(attempt.job_id),
            "AttemptId": str(attempt.id),
            "SourceType": attempt.job.source_type,
            "TemplateVersion": template_version,
        },
        "UserMetadata": {
            "job_id": str(attempt.job_id),
            "attempt_id": str(attempt.id),
        },
    }


def _output_destinations(job_settings):
    destinations = []
    try:
        groups = job_settings["OutputGroups"]
        for group in groups:
            group_settings = group["OutputGroupSettings"]
            if group_settings["Type"] == "HLS_GROUP_SETTINGS":
                destination = group_settings["HlsGroupSettings"]["Destination"]
            else:
                destination = group_settings["FileGroupSettings"]["Destination"]
            destinations.append(destination)
    except (KeyError, TypeError) as error:
        raise InvalidMediaConvertEvidence("Job output destinations are incomplete.") from error
    return tuple(destinations)


def _matches_reconciliation(job, request):
    try:
        metadata = job["UserMetadata"]
        expected_metadata = request["UserMetadata"]
        return all(
            (
                isinstance(job["Id"], str) and bool(job["Id"]),
                metadata.get("job_id") == expected_metadata["job_id"],
                metadata.get("attempt_id") == expected_metadata["attempt_id"],
                job["JobTemplate"] == request["JobTemplate"],
                job["Settings"]["Inputs"][0]["FileInput"]
                == request["Settings"]["Inputs"][0]["FileInput"],
                _output_destinations(job["Settings"])
                == _output_destinations(request["Settings"]),
            )
        )
    except (KeyError, IndexError, TypeError, InvalidMediaConvertEvidence):
        return False


def match_reconciliation_job(jobs, request):
    matches = [job for job in jobs if _matches_reconciliation(job, request)]
    if len(matches) > 1:
        raise AmbiguousReconciliation("Multiple MediaConvert jobs match the submission intent.")
    return matches[0] if matches else None


def _provider_snapshot(response):
    try:
        job = response["Job"]
        job_id = job["Id"]
        status = job["Status"]
    except (KeyError, TypeError) as error:
        raise InvalidMediaConvertEvidence("MediaConvert returned incomplete Job evidence.") from error
    if not isinstance(job_id, str) or not job_id or status not in _PROVIDER_STATUSES:
        raise InvalidMediaConvertEvidence("MediaConvert returned invalid Job evidence.")
    raw_percent = job.get("JobPercentComplete")
    if raw_percent is None:
        percent = None
    elif (
        isinstance(raw_percent, (int, float, Decimal))
        and not isinstance(raw_percent, bool)
        and 0 <= raw_percent <= 100
    ):
        percent = float(raw_percent)
    else:
        raise InvalidMediaConvertEvidence("MediaConvert returned an invalid percentage.")
    phase = job.get("CurrentPhase")
    if phase is not None and not isinstance(phase, str):
        raise InvalidMediaConvertEvidence("MediaConvert returned an invalid phase.")
    warnings = job.get("Warnings", [])
    output_details = job.get("OutputGroupDetails", [])
    if not isinstance(warnings, list) or not isinstance(output_details, list):
        raise InvalidMediaConvertEvidence("MediaConvert returned invalid output evidence.")
    return ProviderSnapshot(
        job_id=job_id,
        status=status,
        phase=phase,
        percent_complete=percent,
        warnings=tuple(warnings),
        output_group_details=tuple(output_details),
    )


def _default_client():
    import boto3

    return boto3.client("mediaconvert", region_name=settings.AWS_REGION)


class MediaConvertGateway:
    def __init__(self, client=None):
        self._client = client or _default_client()

    def probe(self, source_s3_uri):
        return self._client.probe(InputFiles=[{"FileUrl": source_s3_uri}])

    def create_job(self, request):
        response = self._client.create_job(**request)
        try:
            job_id = response["Job"]["Id"]
        except (KeyError, TypeError) as error:
            raise InvalidMediaConvertEvidence("MediaConvert did not return a Job ID.") from error
        if not isinstance(job_id, str) or not job_id:
            raise InvalidMediaConvertEvidence("MediaConvert did not return a Job ID.")
        return job_id

    def list_jobs(self):
        jobs = []
        next_token = None
        for _ in range(10):
            params = {"MaxResults": 20, "Order": "DESCENDING"}
            if next_token is not None:
                params["NextToken"] = next_token
            response = self._client.list_jobs(**params)
            page_jobs = response.get("Jobs", [])
            if not isinstance(page_jobs, list):
                raise InvalidMediaConvertEvidence("MediaConvert returned an invalid Job list.")
            jobs.extend(page_jobs)
            next_token = response.get("NextToken")
            if next_token is None:
                return tuple(jobs)
            if not isinstance(next_token, str) or not next_token:
                raise InvalidMediaConvertEvidence("MediaConvert returned an invalid pagination token.")
        raise InvalidMediaConvertEvidence("MediaConvert Job reconciliation exceeded its page limit.")

    def get_job(self, job_id):
        return _provider_snapshot(self._client.get_job(Id=job_id))

    def cancel_job(self, job_id):
        self._client.cancel_job(Id=job_id)
