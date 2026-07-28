from django.contrib.auth import authenticate, login
from django.db import transaction
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from files.models import Media
from users.models import User

from .authentication import SessionUserAuthentication, has_identity_scope, has_publishing_scope
from .permissions import forbidden, unauthorized
from .serializers import ExternalMediaSerializer


def identity_payload(user: User) -> dict[str, object]:
    return {
        "id": str(user.id),
        "username": user.username,
        "is_active": bool(user.is_active),
        "session_version": user.session_version,
    }


class PrivateLoginView(APIView):
    authentication_classes = []
    permission_classes = []
    parser_classes = [JSONParser]

    def post(self, request):
        identifier = request.data.get("username") or request.data.get("email")
        password = request.data.get("password")
        user = authenticate(request, username=identifier, password=password)
        if user is None or not user.is_active:
            return Response({"detail": "invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)

        login(request, user)
        return Response(identity_payload(user))


class SessionUserView(APIView):
    authentication_classes = [SessionUserAuthentication]
    permission_classes = []

    def get(self, request):
        if not has_identity_scope(request):
            return unauthorized()
        if not request.user.is_authenticated:
            return unauthorized()
        if not request.user.is_active:
            return forbidden()
        return Response(identity_payload(request.user))


class ExternalMediaView(APIView):
    authentication_classes = []
    permission_classes = []
    parser_classes = [JSONParser]

    def _require_scope(self, request):
        if not has_publishing_scope(request):
            return unauthorized()
        return None

    def post(self, request):
        denied = self._require_scope(request)
        if denied:
            return denied

        backend_media_id = request.data.get("backend_media_id")
        with transaction.atomic():
            existing = Media.objects.select_for_update().filter(backend_media_id=backend_media_id).first()
            if existing:
                return Response(ExternalMediaSerializer(existing).data, status=status.HTTP_200_OK)
            serializer = ExternalMediaSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            media = serializer.save()
        return Response(ExternalMediaSerializer(media).data, status=status.HTTP_201_CREATED)


class ExternalMediaDetailView(APIView):
    authentication_classes = []
    permission_classes = []
    parser_classes = [JSONParser]

    def patch(self, request, backend_media_id):
        if not has_publishing_scope(request):
            return unauthorized()

        with transaction.atomic():
            media = Media.objects.select_for_update().filter(backend_media_id=backend_media_id).first()
            if media is None:
                return Response({"detail": "media not found"}, status=status.HTTP_404_NOT_FOUND)
            if request.data.get("version") != media.external_sync_version:
                return Response({"detail": "stale version"}, status=status.HTTP_409_CONFLICT)
            serializer = ExternalMediaSerializer(media, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            media.external_sync_version += 1
            serializer.save()
        return Response(ExternalMediaSerializer(media).data, status=status.HTTP_200_OK)
