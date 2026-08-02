from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from files.models import BrowserUploadSession
from files.services.s3_uploads import S3UploadGateway
from files.services.upload_lease import UploadQueueBlocked, acquire_upload_lease
from files.services.upload_sessions import (
    CreateFileSession,
    CreateHlsSession,
    InvalidUploadCommand,
    UploadIdempotencyConflict,
    create_file_session,
    create_hls_session,
)
from users.permissions import IsSiteAdministrator


def _gateway():
    return S3UploadGateway()


class StrictSerializer(serializers.Serializer):
    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({field: ["Unknown field."] for field in sorted(unknown)})
        return attrs


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
