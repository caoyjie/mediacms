from dataclasses import dataclass

from botocore.exceptions import ClientError
from django.conf import settings


class InvalidManagedObject(ValueError):
    pass


class InvalidObjectEvidence(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ObjectEvidence:
    key: str
    size: int
    content_type: str
    checksum: str


def _stored_checksum(response):
    algorithms = (
        ("sha256", "ChecksumSHA256"),
        ("sha1", "ChecksumSHA1"),
        ("crc64nvme", "ChecksumCRC64NVME"),
        ("crc32c", "ChecksumCRC32C"),
        ("crc32", "ChecksumCRC32"),
    )
    for algorithm, field in algorithms:
        value = response.get(field)
        if isinstance(value, str) and value:
            return f"{algorithm}:{value}"
    raise InvalidObjectEvidence("S3 returned no stored object checksum.")


def _default_s3_client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        config=Config(signature_version="s3v4"),
    )


def _managed_key(key):
    if not isinstance(key, str):
        raise InvalidManagedObject("S3 key is outside managed media storage.")
    parts = key.split("/")
    invalid = (
        not key,
        key.startswith("/"),
        key.endswith("/"),
        parts[0] not in {"uploads", "originals", "candidates"},
        len(parts) < 2,
        any(part in {"", ".", ".."} for part in parts),
    )
    if any(invalid):
        raise InvalidManagedObject("S3 key is outside managed media storage.")
    return key


def _attempt_candidate_prefix(prefix):
    if not isinstance(prefix, str):
        raise InvalidManagedObject("Candidate prefix is not an exact attempt root.")
    parts = prefix.split("/")
    if (
        len(parts) != 4
        or parts[0] != "candidates"
        or not parts[1]
        or not parts[2]
        or parts[3] != ""
        or parts[1] in {".", ".."}
        or parts[2] in {".", ".."}
    ):
        raise InvalidManagedObject("Candidate prefix is not an exact attempt root.")
    return prefix


class ProcessingStorageGateway:
    def __init__(self, client=None):
        self._client = client or _default_s3_client()
        self._bucket = settings.AWS_MEDIA_BUCKET
        if not self._bucket:
            raise ValueError("AWS_MEDIA_BUCKET is not configured")
        self._presign_ttl = settings.AWS_PROCESSING_PRESIGN_TTL_SECONDS
        self._manifest_max_bytes = settings.AWS_MANIFEST_MAX_BYTES
        self._candidate_list_max_objects = settings.AWS_CANDIDATE_LIST_MAX_OBJECTS

    def copy_exact(self, source_key, destination_key):
        self._client.copy_object(
            Bucket=self._bucket,
            Key=_managed_key(destination_key),
            CopySource={"Bucket": self._bucket, "Key": _managed_key(source_key)},
            MetadataDirective="COPY",
            ChecksumAlgorithm="SHA256",
        )

    def head_exact(self, key):
        exact_key = _managed_key(key)
        response = self._client.head_object(
            Bucket=self._bucket,
            Key=exact_key,
            ChecksumMode="ENABLED",
        )
        try:
            evidence = ObjectEvidence(
                key=exact_key,
                size=response["ContentLength"],
                content_type=response["ContentType"],
                checksum=_stored_checksum(response),
            )
        except (KeyError, TypeError) as error:
            raise InvalidObjectEvidence("S3 returned incomplete object evidence.") from error
        if evidence.size <= 0 or not evidence.content_type or not evidence.checksum:
            raise InvalidObjectEvidence("S3 returned invalid object evidence.")
        return evidence

    def presign_get(self, key):
        return self._client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": self._bucket, "Key": _managed_key(key)},
            ExpiresIn=self._presign_ttl,
            HttpMethod="GET",
        )

    def get_text(self, key):
        response = self._client.get_object(
            Bucket=self._bucket,
            Key=_managed_key(key),
            Range=f"bytes=0-{self._manifest_max_bytes}",
        )
        try:
            content_length = response["ContentLength"]
            body = response["Body"]
        except (KeyError, TypeError) as error:
            raise InvalidObjectEvidence("S3 returned incomplete object content.") from error
        if content_length > self._manifest_max_bytes:
            raise InvalidObjectEvidence("Object exceeds the manifest size limit.")
        raw = body.read(self._manifest_max_bytes + 1)
        if len(raw) > self._manifest_max_bytes:
            raise InvalidObjectEvidence("Object exceeds the manifest size limit.")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise InvalidObjectEvidence("Object is not valid UTF-8 text.") from error

    def list_attempt_candidates(self, prefix):
        exact_prefix = _attempt_candidate_prefix(prefix)
        keys = []
        continuation_token = None
        while True:
            params = {"Bucket": self._bucket, "Prefix": exact_prefix}
            if continuation_token is not None:
                params["ContinuationToken"] = continuation_token
            response = self._client.list_objects_v2(**params)
            for item in response.get("Contents", []):
                try:
                    key = _managed_key(item["Key"])
                except (KeyError, TypeError) as error:
                    raise InvalidObjectEvidence("S3 returned incomplete object listing.") from error
                if not key.startswith(exact_prefix):
                    raise InvalidObjectEvidence("S3 returned an object outside the attempt prefix.")
                keys.append(key)
                if len(keys) > self._candidate_list_max_objects:
                    raise InvalidObjectEvidence("Candidate listing exceeds the object limit.")
            if response.get("IsTruncated") is not True:
                break
            continuation_token = response.get("NextContinuationToken")
            if not isinstance(continuation_token, str) or not continuation_token:
                raise InvalidObjectEvidence("S3 omitted the continuation token.")
        return tuple(keys)

    def delete_exact(self, key):
        try:
            self._client.delete_object(Bucket=self._bucket, Key=_managed_key(key))
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchKey", "NotFound"}:
                raise
