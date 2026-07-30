from django.contrib.auth import authenticate
from django.db import transaction
from django.db.models import F
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from files.models import Media
from users.models import User

from .authentication import (
    BffUserAuthentication,
    has_identity_scope,
    has_publishing_scope,
)
from .permissions import forbidden, unauthorized
from .profile_serializers import (
    CurrentProfileSerializer,
    PasswordChangeSerializer,
    ProfileLogoSerializer,
)
from .serializers import (
    ExternalMediaSerializer,
    reconcile_external_subtitles,
)


def identity_payload(user: User) -> dict[str, object]:
    return {
        "id": str(user.id),
        "username": user.username,
        "is_active": bool(user.is_active),
        "session_version": user.session_version,
    }


def current_profile(user: User, request):
    return CurrentProfileSerializer(
        user,
        context={"request": request},
    ).data


class CurrentProfileView(APIView):
    authentication_classes = [BffUserAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def get(self, request):
        return Response(current_profile(request.user, request))

    def patch(self, request):
        serializer = CurrentProfileSerializer(
            request.user,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(current_profile(request.user, request))


class ProfileLogoView(APIView):
    authentication_classes = [BffUserAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        serializer = ProfileLogoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        request.user.logo = serializer.validated_data["logo"]
        request.user.save(update_fields=["logo"])
        return Response(current_profile(request.user, request))


class ProfilePasswordView(APIView):
    authentication_classes = [BffUserAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request):
        serializer = PasswordChangeSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            user = request.user
            user.set_password(serializer.validated_data["new_password"])
            User.objects.filter(pk=user.pk).update(
                password=user.password,
                session_version=F("session_version") + 1,
            )
            user.refresh_from_db()

        return Response({"session_version": user.session_version})


class PrivateLoginView(APIView):
    authentication_classes = []
    permission_classes = []
    parser_classes = [JSONParser]

    def post(self, request):
        if not has_identity_scope(request):
            return unauthorized()

        identifier = request.data.get("username") or request.data.get("email")
        password = request.data.get("password")
        user = authenticate(request, username=identifier, password=password)
        if user is None or not user.is_active:
            return Response({"detail": "invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)

        return Response(identity_payload(user))


class IdentityValidationView(APIView):
    authentication_classes = []
    permission_classes = []
    parser_classes = [JSONParser]

    def post(self, request):
        if not has_identity_scope(request):
            return unauthorized()

        user_id = request.data.get("id")
        session_version = request.data.get("session_version")
        valid_version = isinstance(session_version, int) and not isinstance(
            session_version, bool
        )
        if not user_id or not valid_version:
            return unauthorized()

        try:
            user = User.objects.get(pk=user_id)
        except (TypeError, ValueError, User.DoesNotExist):
            return unauthorized()

        if not user.is_active:
            return forbidden()
        if user.session_version != session_version:
            return unauthorized()
        return Response(identity_payload(user))


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
                serializer = ExternalMediaSerializer(
                    existing,
                    data=request.data,
                )
                serializer.is_valid(raise_exception=True)
                if "subtitles" in serializer.validated_data:
                    reconcile_external_subtitles(
                        existing,
                        serializer.validated_data["subtitles"],
                    )
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
