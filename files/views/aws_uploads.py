from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from files.models import BrowserUploadSession
from files.services.hls_package import HlsInventoryEntry
from files.services.processing_storage import ProcessingStorageGateway
from files.services.s3_uploads import S3UploadGateway
from files.services.upload_lease import (
    UploadLeaseConflict,
    UploadLeaseExpired,
    UploadQueueBlocked,
    acquire_upload_lease,
    heartbeat_upload_lease,
)
from files.services.upload_sessions import (
    CreateFileSession,
    CreateHlsSession,
    InvalidUploadCommand,
    PartUploadRequest,
    UploadIdempotencyConflict,
    UploadRevisionConflict,
    UploadVerificationFailed,
    complete_file_upload,
    complete_hls_upload,
    cancel_upload,
    create_file_session,
    create_hls_session,
    issue_part_urls,
    issue_hls_object_url,
    pause_upload,
    reconcile_parts,
    register_hls_inventory,
    resume_upload,
)
from users.permissions import IsSiteAdministrator


def _gateway():
    return S3UploadGateway()


def _processing_storage():
    return ProcessingStorageGateway()


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        unknown = set(data) - set(self.fields) if isinstance(data, dict) else set()
        if unknown:
            raise serializers.ValidationError({field: ["Unknown field."] for field in sorted(unknown)})
        return super().to_internal_value(data)


class UploadCreateSerializer(StrictSerializer):
    source_kind = serializers.ChoiceField(choices=("file", "hls"))
    title = serializers.CharField(max_length=100)
    media_type = serializers.ChoiceField(choices=("video", "audio"), required=False)
    filename = serializers.CharField(max_length=255, required=False)
    size = serializers.IntegerField(min_value=1, required=False)
    content_type = serializers.CharField(max_length=255, required=False)
    fingerprint = serializers.CharField(max_length=255, required=False)
    total_size = serializers.IntegerField(min_value=1, required=False)
    file_count = serializers.IntegerField(min_value=1, required=False)
    package_fingerprint = serializers.CharField(max_length=255, required=False)


class LeaseSerializer(StrictSerializer):
    lease_seconds = serializers.IntegerField(min_value=15, max_value=300, default=60)


class PartSerializer(StrictSerializer):
    part_number = serializers.IntegerField(min_value=1, max_value=10_000)
    checksum_sha256 = serializers.CharField(max_length=255)


class PartUrlSerializer(StrictSerializer):
    parts = PartSerializer(many=True)


class HlsEntrySerializer(StrictSerializer):
    path = serializers.CharField(max_length=1024)
    size = serializers.IntegerField(min_value=1)
    compressed_size = serializers.IntegerField(min_value=1)
    content_type = serializers.CharField(max_length=255)
    checksum_sha256 = serializers.CharField(max_length=255)
    is_symlink = serializers.BooleanField(default=False)


class HlsInventorySerializer(StrictSerializer):
    entries = HlsEntrySerializer(many=True)


class CompleteSerializer(StrictSerializer):
    manifest_bodies = serializers.DictField(
        child=serializers.CharField(allow_blank=True),
        required=False,
    )


def _error(code, detail, http_status):
    return Response({"code": code, "detail": detail}, status=http_status)


def _project_session(session):
    session = BrowserUploadSession.objects.select_related("job").get(pk=session.pk)
    return {
        "id": str(session.id),
        "job_id": str(session.job_id),
        "media_id": session.job.media_id,
        "source_kind": session.source_kind,
        "status": session.status,
        "stage": session.job.stage,
        "revision": session.revision,
        "confirmed_bytes": session.confirmed_bytes,
        "total_bytes": session.expected_total_size,
        "confirmed_files": session.confirmed_file_count,
        "total_files": session.expected_file_count,
        "part_size": session.part_size,
        "expires_at": session.expires_at,
    }


def _mutation_headers(request, *, revision=False):
    idempotency_key = request.headers.get("Idempotency-Key", "")
    owner_token = request.headers.get("Upload-Lease-Token", "")
    if not idempotency_key:
        return None, _error(
            "idempotency_key_required",
            "Idempotency-Key header is required.",
            status.HTTP_400_BAD_REQUEST,
        )
    if not owner_token:
        return None, _error(
            "lease_token_required",
            "Upload-Lease-Token header is required.",
            status.HTTP_400_BAD_REQUEST,
        )
    expected_revision = None
    if revision:
        raw_revision = request.headers.get("If-Match", "").strip().strip('"')
        if not raw_revision.isdigit():
            return None, _error(
                "revision_required",
                "If-Match must contain the upload revision.",
                status.HTTP_428_PRECONDITION_REQUIRED,
            )
        expected_revision = int(raw_revision)
    return (idempotency_key, owner_token, expected_revision), None


def _service_error(error):
    if isinstance(error, UploadRevisionConflict):
        return Response(
            {"code": "revision_conflict", "current_revision": error.current_revision},
            status=status.HTTP_412_PRECONDITION_FAILED,
        )
    if isinstance(error, UploadIdempotencyConflict):
        return _error("idempotency_conflict", str(error), status.HTTP_409_CONFLICT)
    if isinstance(error, (UploadLeaseConflict, UploadLeaseExpired)):
        return _error("upload_locked", str(error), status.HTTP_423_LOCKED)
    if isinstance(error, UploadVerificationFailed):
        return _error("verification_failed", str(error), status.HTTP_409_CONFLICT)
    return _error("invalid_request", str(error), status.HTTP_400_BAD_REQUEST)


def _signed_request(request):
    return {
        "url": request.url,
        "headers": request.headers,
        "expires_in": request.expires_in,
    }


class UploadCollectionView(APIView):
    permission_classes = (IsSiteAdministrator,)

    def get(self, request):
        sessions = BrowserUploadSession.objects.filter(owner=request.user).select_related("job")[:100]
        return Response([_project_session(session) for session in sessions])

    def post(self, request):
        idempotency_key = request.headers.get("Idempotency-Key", "")
        if not idempotency_key:
            return _error("idempotency_key_required", "Idempotency-Key header is required.", status.HTTP_400_BAD_REQUEST)
        serializer = UploadCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"code": "invalid_request", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = serializer.validated_data
        try:
            if data["source_kind"] == "file":
                required = ("media_type", "filename", "size", "content_type", "fingerprint")
                if any(field not in data for field in required):
                    raise InvalidUploadCommand("File upload fields are incomplete.")
                created = create_file_session(
                    request.user,
                    CreateFileSession(
                        data["title"],
                        data["media_type"],
                        data["filename"],
                        data["size"],
                        data["content_type"],
                        data["fingerprint"],
                        idempotency_key,
                    ),
                    _gateway(),
                )
            else:
                required = ("total_size", "file_count", "package_fingerprint")
                if any(field not in data for field in required):
                    raise InvalidUploadCommand("HLS upload fields are incomplete.")
                created = create_hls_session(
                    request.user,
                    CreateHlsSession(
                        data["title"],
                        data["total_size"],
                        data["file_count"],
                        data["package_fingerprint"],
                        idempotency_key,
                    ),
                )
        except InvalidUploadCommand as error:
            return _error("invalid_request", str(error), status.HTTP_400_BAD_REQUEST)
        except UploadIdempotencyConflict as error:
            return _error("idempotency_conflict", str(error), status.HTTP_409_CONFLICT)
        session = BrowserUploadSession.objects.get(pk=created.session_id)
        return Response(_project_session(session), status=status.HTTP_201_CREATED)


class UploadDetailView(APIView):
    permission_classes = (IsSiteAdministrator,)

    def get(self, request, session_id):
        session = get_object_or_404(BrowserUploadSession, pk=session_id, owner=request.user)
        return Response(_project_session(session))


class UploadLeaseAcquireView(APIView):
    permission_classes = (IsSiteAdministrator,)

    def post(self, request, session_id):
        session = get_object_or_404(BrowserUploadSession, pk=session_id, owner=request.user)
        token = request.headers.get("Upload-Lease-Token", "")
        if not request.headers.get("Idempotency-Key"):
            return _error("idempotency_key_required", "Idempotency-Key header is required.", status.HTTP_400_BAD_REQUEST)
        if not token:
            return _error("lease_token_required", "Upload-Lease-Token header is required.", status.HTTP_400_BAD_REQUEST)
        serializer = LeaseSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"code": "invalid_request", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            acquire_upload_lease(session.id, token, serializer.validated_data["lease_seconds"])
        except UploadQueueBlocked as error:
            return Response(
                {"code": "upload_queued", "queue_position": error.position},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(_project_session(session))


class UploadPartUrlsView(APIView):
    permission_classes = (IsSiteAdministrator,)

    def post(self, request, session_id, object_id=None):
        session = get_object_or_404(BrowserUploadSession, pk=session_id, owner=request.user)
        headers, error_response = _mutation_headers(request)
        if error_response:
            return error_response
        serializer = PartUrlSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"code": "invalid_request", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        part_requests = tuple(
            PartUploadRequest(part["part_number"], part["checksum_sha256"])
            for part in serializer.validated_data["parts"]
        )
        try:
            signed = issue_part_urls(
                session.id,
                headers[1],
                part_requests,
                _gateway(),
                object_id=object_id,
            )
        except (InvalidUploadCommand, UploadLeaseConflict, UploadLeaseExpired) as error:
            return _service_error(error)
        return Response({"requests": [_signed_request(item) for item in signed]})


class UploadReconcileView(APIView):
    permission_classes = (IsSiteAdministrator,)

    def post(self, request, session_id):
        session = get_object_or_404(BrowserUploadSession, pk=session_id, owner=request.user)
        headers, error_response = _mutation_headers(request)
        if error_response:
            return error_response
        try:
            reconcile_parts(session.id, headers[1], _gateway())
        except (InvalidUploadCommand, UploadLeaseConflict, UploadLeaseExpired) as error:
            return _service_error(error)
        return Response(_project_session(session))


class UploadCompleteView(APIView):
    permission_classes = (IsSiteAdministrator,)

    def post(self, request, session_id):
        session = get_object_or_404(BrowserUploadSession, pk=session_id, owner=request.user)
        headers, error_response = _mutation_headers(request, revision=True)
        if error_response:
            return error_response
        serializer = CompleteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"code": "invalid_request", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            if session.source_kind == "hls":
                complete_hls_upload(
                    session.id,
                    headers[1],
                    headers[0],
                    headers[2],
                    serializer.validated_data.get("manifest_bodies", {}),
                    _gateway(),
                )
            else:
                complete_file_upload(
                    session.id,
                    headers[1],
                    headers[0],
                    headers[2],
                    _gateway(),
                    _processing_storage(),
                )
        except (
            InvalidUploadCommand,
            UploadIdempotencyConflict,
            UploadLeaseConflict,
            UploadLeaseExpired,
            UploadRevisionConflict,
            UploadVerificationFailed,
        ) as error:
            return _service_error(error)
        return Response(_project_session(session))


class UploadObjectRegisterView(APIView):
    permission_classes = (IsSiteAdministrator,)

    def post(self, request, session_id):
        session = get_object_or_404(BrowserUploadSession, pk=session_id, owner=request.user)
        headers, error_response = _mutation_headers(request)
        if error_response:
            return error_response
        serializer = HlsInventorySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"code": "invalid_request", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        entries = tuple(HlsInventoryEntry(**entry) for entry in serializer.validated_data["entries"])
        try:
            registered = register_hls_inventory(session.id, headers[1], entries, _gateway())
        except (
            InvalidUploadCommand,
            UploadIdempotencyConflict,
            UploadLeaseConflict,
            UploadLeaseExpired,
        ) as error:
            return _service_error(error)
        return Response(
            {
                "objects": [
                    {"id": str(item.object_id), "path": item.relative_path, "strategy": item.strategy}
                    for item in registered
                ],
                "revision": BrowserUploadSession.objects.get(pk=session.id).revision,
            }
        )


class UploadObjectUrlView(APIView):
    permission_classes = (IsSiteAdministrator,)

    def post(self, request, session_id, object_id):
        session = get_object_or_404(BrowserUploadSession, pk=session_id, owner=request.user)
        headers, error_response = _mutation_headers(request)
        if error_response:
            return error_response
        try:
            signed = issue_hls_object_url(session.id, headers[1], object_id, _gateway())
        except (InvalidUploadCommand, UploadLeaseConflict, UploadLeaseExpired) as error:
            return _service_error(error)
        return Response({"request": _signed_request(signed)})


class UploadLeaseHeartbeatView(APIView):
    permission_classes = (IsSiteAdministrator,)

    def post(self, request, session_id):
        session = get_object_or_404(BrowserUploadSession, pk=session_id, owner=request.user)
        headers, error_response = _mutation_headers(request)
        if error_response:
            return error_response
        serializer = LeaseSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"code": "invalid_request", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            heartbeat_upload_lease(session.id, headers[1], serializer.validated_data["lease_seconds"])
        except (UploadLeaseConflict, UploadLeaseExpired) as error:
            return _service_error(error)
        return Response(_project_session(session))


class UploadPauseView(APIView):
    permission_classes = (IsSiteAdministrator,)

    def post(self, request, session_id):
        session = get_object_or_404(BrowserUploadSession, pk=session_id, owner=request.user)
        headers, error_response = _mutation_headers(request)
        if error_response:
            return error_response
        try:
            pause_upload(session.id, headers[1])
        except (InvalidUploadCommand, UploadLeaseConflict, UploadLeaseExpired) as error:
            return _service_error(error)
        return Response(_project_session(session))


class UploadResumeView(APIView):
    permission_classes = (IsSiteAdministrator,)

    def post(self, request, session_id):
        session = get_object_or_404(BrowserUploadSession, pk=session_id, owner=request.user)
        if not request.headers.get("Idempotency-Key"):
            return _error(
                "idempotency_key_required",
                "Idempotency-Key header is required.",
                status.HTTP_400_BAD_REQUEST,
            )
        try:
            resume_upload(session.id)
        except InvalidUploadCommand as error:
            return _service_error(error)
        return Response(_project_session(session))


class UploadCancelView(APIView):
    permission_classes = (IsSiteAdministrator,)

    def post(self, request, session_id):
        session = get_object_or_404(BrowserUploadSession, pk=session_id, owner=request.user)
        headers, error_response = _mutation_headers(request)
        if error_response:
            return error_response
        try:
            cancel_upload(session.id, headers[1], _gateway())
        except (InvalidUploadCommand, UploadLeaseConflict, UploadLeaseExpired) as error:
            return _service_error(error)
        return Response(_project_session(session))
