from django.contrib.auth import authenticate, login
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from users.models import User

from .authentication import SessionUserAuthentication, has_identity_scope
from .permissions import forbidden, unauthorized


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
