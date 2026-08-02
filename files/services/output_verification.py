from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.db import transaction

from files.models import ArtifactPurpose, AttemptArtifact, MediaJobAttempt
from files.services.hls_package import UnsafeHlsPackage, _manifest_references
from files.services.processing_storage import ObjectEvidence


class InvalidMediaConvertOutput(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VerifiedOutput:
    kind: str
    evidence: ObjectEvidence


@dataclass(frozen=True, slots=True)
class VerifiedOutputSet:
    manifest_key: str
    outputs: tuple[VerifiedOutput, ...]


_CONTENT_TYPES = {
    ".m3u8": {
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
        "audio/mpegurl",
        "audio/x-mpegurl",
    },
    ".ts": {"video/mp2t"},
    ".m4s": {
        "video/iso.segment",
        "audio/iso.segment",
        "application/octet-stream",
    },
    ".mp4": {"video/mp4", "audio/mp4"},
    ".aac": {"audio/aac", "audio/mpeg", "application/octet-stream"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".png": {"image/png"},
    ".webp": {"image/webp"},
}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_OUTPUT_ORDER = {"hls_master": 0, "hls_variant": 1, "hls_segment": 2, "poster": 3}


def _candidate_prefix(attempt):
    return f"candidates/{attempt.job.media_id}/{attempt.id}/"


def _provider_key(uri, prefix):
    if not isinstance(uri, str):
        raise InvalidMediaConvertOutput("MediaConvert output path is invalid.")
    invalid_uri = (
        not uri,
        "%" in uri,
        "\\" in uri,
        "\x00" in uri,
    )
    if any(invalid_uri):
        raise InvalidMediaConvertOutput("MediaConvert output path is invalid.")
    parsed = urlsplit(uri)
    if any(
        (
            parsed.scheme != "s3",
            parsed.netloc != settings.AWS_MEDIA_BUCKET,
            bool(parsed.query),
            bool(parsed.fragment),
            not parsed.path.startswith("/"),
        )
    ):
        raise InvalidMediaConvertOutput("MediaConvert output path is outside the attempt.")
    key = unquote(parsed.path[1:])
    parts = key.split("/")
    invalid_key = (
        not key.startswith(prefix),
        any(part in {"", ".", ".."} for part in parts),
        key.endswith("/"),
    )
    if any(invalid_key):
        raise InvalidMediaConvertOutput("MediaConvert output path is outside the attempt.")
    return key


def _string_list(value, label):
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise InvalidMediaConvertOutput(f"MediaConvert {label} path evidence is invalid.")
    return value


def _provider_business_paths(snapshot, prefix):
    masters = []
    variants = []
    images = []
    for group in snapshot.output_group_details:
        if not isinstance(group, dict):
            raise InvalidMediaConvertOutput("MediaConvert output group evidence is invalid.")
        group_type = group.get("Type")
        details = group.get("OutputDetails", [])
        if not isinstance(details, list):
            raise InvalidMediaConvertOutput("MediaConvert output detail evidence is invalid.")
        output_paths = []
        for detail in details:
            if not isinstance(detail, dict):
                raise InvalidMediaConvertOutput("MediaConvert output detail evidence is invalid.")
            output_paths.extend(_string_list(detail.get("OutputFilePaths", []), "output"))
        if group_type == "HLS_GROUP":
            masters.extend(
                _provider_key(uri, prefix)
                for uri in _string_list(group.get("PlaylistFilePaths", []), "playlist")
            )
            variants.extend(_provider_key(uri, prefix) for uri in output_paths)
        elif group_type == "FILE_GROUP":
            images.extend(_provider_key(uri, prefix) for uri in output_paths)
        else:
            raise InvalidMediaConvertOutput(
                "MediaConvert returned an unsupported output group type."
            )

    if len(masters) != 1:
        raise InvalidMediaConvertOutput("MediaConvert must return one unique master playlist.")
    if not variants or len(set(variants)) != len(variants):
        raise InvalidMediaConvertOutput("MediaConvert variant path evidence is invalid.")
    if any(PurePosixPath(path).suffix.lower() != ".m3u8" for path in masters + variants):
        raise InvalidMediaConvertOutput("MediaConvert playlist path is invalid.")
    return masters[0], tuple(variants), tuple(images)


def _validate_evidence(evidence, expected_key):
    if evidence.key != expected_key:
        raise InvalidMediaConvertOutput("S3 object evidence has a mismatched path.")
    if evidence.size <= 0:
        raise InvalidMediaConvertOutput("MediaConvert output must have nonzero size.")
    if not evidence.content_type or not evidence.checksum:
        raise InvalidMediaConvertOutput("MediaConvert output evidence is incomplete.")


def _ledger_inventory(attempt, prefix, storage):
    keys = storage.list_attempt_candidates(prefix)
    if not keys or len(set(keys)) != len(keys):
        raise InvalidMediaConvertOutput("Candidate inventory is empty or duplicated.")
    evidence_by_key = {}
    for key in keys:
        if not isinstance(key, str) or not key.startswith(prefix):
            raise InvalidMediaConvertOutput("Candidate inventory path is outside the attempt.")
        evidence = storage.head_exact(key)
        _validate_evidence(evidence, key)
        evidence_by_key[key] = evidence

    with transaction.atomic():
        for key in sorted(evidence_by_key):
            evidence = evidence_by_key[key]
            artifact, created = AttemptArtifact.objects.get_or_create(
                attempt=attempt,
                s3_key=key,
                defaults={
                    "purpose": ArtifactPurpose.CANDIDATE,
                    "size_bytes": evidence.size,
                    "content_type": evidence.content_type,
                    "checksum": evidence.checksum,
                },
            )
            if not created and any(
                (
                    artifact.purpose != ArtifactPurpose.CANDIDATE,
                    artifact.size_bytes != evidence.size,
                    artifact.content_type != evidence.content_type,
                    artifact.checksum != evidence.checksum,
                )
            ):
                raise InvalidMediaConvertOutput(
                    "Candidate inventory conflicts with its artifact ledger."
                )
    return evidence_by_key


def _manifest_closure(master, evidence_by_key, storage, prefix):
    closure = set()
    visited_manifests = set()
    pending = [master]
    while pending:
        manifest = pending.pop()
        if manifest in visited_manifests:
            continue
        if manifest not in evidence_by_key:
            raise InvalidMediaConvertOutput("HLS manifest references a missing object.")
        visited_manifests.add(manifest)
        closure.add(manifest)
        try:
            references = _manifest_references(manifest, storage.get_text(manifest))
        except UnsafeHlsPackage as error:
            raise InvalidMediaConvertOutput("HLS manifest is unsafe.") from error
        for reference in references:
            if not reference.startswith(prefix) or reference not in evidence_by_key:
                raise InvalidMediaConvertOutput("HLS manifest references a missing object.")
            closure.add(reference)
            if PurePosixPath(reference).suffix.lower() == ".m3u8":
                pending.append(reference)
    return closure


def _validate_content_type(evidence):
    suffix = PurePosixPath(evidence.key).suffix.lower()
    content_type = evidence.content_type.split(";", 1)[0].strip().lower()
    if content_type not in _CONTENT_TYPES.get(suffix, set()):
        raise InvalidMediaConvertOutput("MediaConvert output has an invalid content type.")


def _typed_outputs(master, closure, images, evidence_by_key):
    outputs = []
    for key in closure:
        evidence = evidence_by_key[key]
        _validate_content_type(evidence)
        if key == master:
            kind = "hls_master"
        elif PurePosixPath(key).suffix.lower() == ".m3u8":
            kind = "hls_variant"
        else:
            kind = "hls_segment"
        outputs.append(VerifiedOutput(kind, evidence))
    for key in images:
        evidence = evidence_by_key[key]
        _validate_content_type(evidence)
        outputs.append(VerifiedOutput("poster", evidence))
    return tuple(sorted(outputs, key=lambda item: (_OUTPUT_ORDER[item.kind], item.evidence.key)))


def verify_mediaconvert_outputs(attempt_id, snapshot, storage):
    attempt = MediaJobAttempt.objects.select_related("job__media").get(pk=attempt_id)
    completion_invalid = (
        snapshot.status != "COMPLETE",
        snapshot.job_id != attempt.mediaconvert_job_id,
        not attempt.checkpoints.filter(
            name="mediaconvert_complete",
            status="completed",
        ).exists(),
    )
    if any(completion_invalid):
        raise InvalidMediaConvertOutput("MediaConvert completion is not proven.")
    prefix = _candidate_prefix(attempt)
    evidence_by_key = _ledger_inventory(attempt, prefix, storage)
    master, variants, images = _provider_business_paths(snapshot, prefix)
    if attempt.job.media.media_type == "video":
        if len(images) != 1 or PurePosixPath(images[0]).suffix.lower() not in _IMAGE_SUFFIXES:
            raise InvalidMediaConvertOutput("Video output requires one image.")
    elif attempt.job.media.media_type == "audio":
        if images:
            raise InvalidMediaConvertOutput("Audio output must not contain an image.")
    else:
        raise InvalidMediaConvertOutput("Media type is not supported.")

    closure = _manifest_closure(master, evidence_by_key, storage, prefix)
    missing_business_paths = set(variants) - closure
    if missing_business_paths:
        raise InvalidMediaConvertOutput(
            "A MediaConvert variant is not referenced by the master manifest."
        )
    closure_variants = {
        key
        for key in closure
        if key != master and PurePosixPath(key).suffix.lower() == ".m3u8"
    }
    if closure_variants - set(variants):
        raise InvalidMediaConvertOutput(
            "The master manifest contains a variant not declared by MediaConvert."
        )
    missing_images = set(images) - set(evidence_by_key)
    if missing_images:
        raise InvalidMediaConvertOutput("MediaConvert image output is missing.")
    outputs = _typed_outputs(master, closure, images, evidence_by_key)
    return VerifiedOutputSet(manifest_key=master, outputs=outputs)
