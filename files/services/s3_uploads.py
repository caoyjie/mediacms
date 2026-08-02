import base64
import binascii
from dataclasses import dataclass

from django.conf import settings


class InvalidUploadObjectKey(ValueError):
    pass


class InvalidS3Evidence(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PresignedRequest:
    url: str
    headers: dict[str, str]
    expires_in: int


@dataclass(frozen=True, slots=True)
class S3Part:
    part_number: int
    etag: str
    size: int
    checksum_sha256: str


@dataclass(frozen=True, slots=True)
class S3ObjectEvidence:
    size: int
    content_type: str
    etag: str
    checksum_sha256: str


def _default_s3_client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        config=Config(signature_version="s3v4"),
    )


def _exact_upload_key(key):
    invalid = (
        not key,
        not key.startswith("uploads/"),
        key.startswith("/"),
        key.endswith("/"),
        "//" in key,
        any(part in {"", ".", ".."} for part in key.split("/")),
    )
    if any(invalid):
        raise InvalidUploadObjectKey("S3 key is outside the browser upload scope.")
    return key


def _validate_part_number(part_number):
    if part_number < 1 or part_number > 10_000:
        raise ValueError("multipart part number is outside the S3 range")


def _validate_sha256(value):
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("invalid base64 SHA-256 checksum") from error
    if len(decoded) != 32:
        raise ValueError("SHA-256 checksum must decode to 32 bytes")


class S3UploadGateway:
    def __init__(self, client=None):
        self._client = client or _default_s3_client()
        self._bucket = settings.AWS_MEDIA_BUCKET
        if not self._bucket:
            raise ValueError("AWS_MEDIA_BUCKET is not configured")
        self._presign_ttl = settings.AWS_UPLOAD_PRESIGN_TTL_SECONDS

    def create_multipart(self, key, content_type):
        response = self._client.create_multipart_upload(
            Bucket=self._bucket,
            Key=_exact_upload_key(key),
            ContentType=content_type,
            ChecksumAlgorithm="SHA256",
            ChecksumType="COMPOSITE",
        )
        upload_id = response.get("UploadId")
        if not isinstance(upload_id, str) or not upload_id:
            raise InvalidS3Evidence("S3 did not return a multipart upload ID.")
        return upload_id

    def presign_part(self, key, upload_id, part_number, checksum_sha256):
        exact_key = _exact_upload_key(key)
        _validate_part_number(part_number)
        _validate_sha256(checksum_sha256)
        url = self._client.generate_presigned_url(
            ClientMethod="upload_part",
            Params={
                "Bucket": self._bucket,
                "Key": exact_key,
                "UploadId": upload_id,
                "PartNumber": part_number,
                "ChecksumSHA256": checksum_sha256,
            },
            ExpiresIn=self._presign_ttl,
            HttpMethod="PUT",
        )
        return PresignedRequest(
            url=url,
            headers={"x-amz-checksum-sha256": checksum_sha256},
            expires_in=self._presign_ttl,
        )

    def presign_put(self, key, content_type, content_length, checksum_sha256):
        exact_key = _exact_upload_key(key)
        if content_length <= 0:
            raise ValueError("content length must be positive")
        _validate_sha256(checksum_sha256)
        url = self._client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": self._bucket,
                "Key": exact_key,
                "ContentType": content_type,
                "ContentLength": content_length,
                "ChecksumSHA256": checksum_sha256,
            },
            ExpiresIn=self._presign_ttl,
            HttpMethod="PUT",
        )
        return PresignedRequest(
            url=url,
            headers={
                "content-type": content_type,
                "content-length": str(content_length),
                "x-amz-checksum-sha256": checksum_sha256,
            },
            expires_in=self._presign_ttl,
        )

    def list_parts(self, key, upload_id):
        exact_key = _exact_upload_key(key)
        parts = []
        marker = None
        while True:
            params = {
                "Bucket": self._bucket,
                "Key": exact_key,
                "UploadId": upload_id,
            }
            if marker is not None:
                params["PartNumberMarker"] = marker
            response = self._client.list_parts(**params)
            for raw_part in response.get("Parts", []):
                try:
                    part = S3Part(
                        part_number=raw_part["PartNumber"],
                        etag=raw_part["ETag"],
                        size=raw_part["Size"],
                        checksum_sha256=raw_part["ChecksumSHA256"],
                    )
                except (KeyError, TypeError) as error:
                    raise InvalidS3Evidence("S3 returned incomplete Part evidence.") from error
                _validate_part_number(part.part_number)
                _validate_sha256(part.checksum_sha256)
                if not part.etag or part.size <= 0:
                    raise InvalidS3Evidence("S3 returned invalid Part evidence.")
                parts.append(part)
            if response.get("IsTruncated") is not True:
                break
            marker = response.get("NextPartNumberMarker")
            if not isinstance(marker, int):
                raise InvalidS3Evidence("S3 omitted the next Part marker.")
        return tuple(parts)

    def complete_multipart(self, key, upload_id, parts):
        exact_key = _exact_upload_key(key)
        if not parts:
            raise ValueError("multipart completion requires at least one Part")
        self._client.complete_multipart_upload(
            Bucket=self._bucket,
            Key=exact_key,
            UploadId=upload_id,
            ChecksumType="COMPOSITE",
            MultipartUpload={
                "Parts": [
                    {
                        "PartNumber": part.part_number,
                        "ETag": part.etag,
                        "ChecksumSHA256": part.checksum_sha256,
                    }
                    for part in parts
                ]
            },
        )

    def head_object(self, key):
        response = self._client.head_object(
            Bucket=self._bucket,
            Key=_exact_upload_key(key),
            ChecksumMode="ENABLED",
        )
        try:
            evidence = S3ObjectEvidence(
                size=response["ContentLength"],
                content_type=response["ContentType"],
                etag=response["ETag"],
                checksum_sha256=response["ChecksumSHA256"],
            )
        except (KeyError, TypeError) as error:
            raise InvalidS3Evidence("S3 returned incomplete object evidence.") from error
        if evidence.size <= 0 or not evidence.content_type or not evidence.etag or not evidence.checksum_sha256:
            raise InvalidS3Evidence("S3 returned invalid object evidence.")
        return evidence

    def abort_multipart(self, key, upload_id):
        self._client.abort_multipart_upload(
            Bucket=self._bucket,
            Key=_exact_upload_key(key),
            UploadId=upload_id,
        )

    def delete_exact_keys(self, keys):
        exact_keys = tuple(_exact_upload_key(key) for key in keys)
        for offset in range(0, len(exact_keys), 1000):
            batch = exact_keys[offset : offset + 1000]
            self._client.delete_objects(
                Bucket=self._bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
            )
