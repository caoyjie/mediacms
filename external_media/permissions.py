from rest_framework import status
from rest_framework.response import Response


def unauthorized() -> Response:
    return Response({"detail": "authentication required"}, status=status.HTTP_401_UNAUTHORIZED)


def forbidden() -> Response:
    return Response({"detail": "account inactive"}, status=status.HTTP_403_FORBIDDEN)
