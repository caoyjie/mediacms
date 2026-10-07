from django.conf import settings
from django.db.models import Q
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import (
    FileUploadParser,
    FormParser,
    JSONParser,
    MultiPartParser,
)
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from cms.permissions import IsAuthorizedToAdd, IsUserOrEditor

from ..methods import is_mediacms_editor
from ..models import Media, Playlist, PlaylistMedia
from ..playlist_operations import PlaylistCapacityExceeded, PlaylistUnavailable, add_media_to_playlists
from ..serializers import MediaSerializer, PlaylistDetailSerializer, PlaylistSerializer


class PlaylistList(APIView):
    """Playlists listings and creation views"""

    permission_classes = (permissions.IsAuthenticatedOrReadOnly, IsAuthorizedToAdd)
    parser_classes = (JSONParser, MultiPartParser, FormParser, FileUploadParser)

    @swagger_auto_schema(
        manual_parameters=[],
        tags=['Playlists'],
        operation_summary='to_be_written',
        operation_description='to_be_written',
        responses={
            200: openapi.Response('response description', PlaylistSerializer(many=True)),
        },
    )
    def get(self, request, format=None):
        pagination_class = api_settings.DEFAULT_PAGINATION_CLASS
        paginator = pagination_class()
        playlists = Playlist.objects.filter().prefetch_related("user")

        if "author" in self.request.query_params:
            author = self.request.query_params["author"].strip()
            playlists = playlists.filter(user__username=author)

        page = paginator.paginate_queryset(playlists, request)

        serializer = PlaylistSerializer(page, many=True, context={"request": request})
        return paginator.get_paginated_response(serializer.data)

    @swagger_auto_schema(
        manual_parameters=[],
        tags=['Playlists'],
        operation_summary='to_be_written',
        operation_description='to_be_written',
    )
    def post(self, request, format=None):
        serializer = PlaylistSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PlaylistDetail(APIView):
    """Playlist related views"""

    permission_classes = (permissions.IsAuthenticatedOrReadOnly, IsUserOrEditor)
    parser_classes = (JSONParser, MultiPartParser, FormParser, FileUploadParser)

    def get_playlist(self, friendly_token):
        try:
            playlist = Playlist.objects.get(friendly_token=friendly_token)
            self.check_object_permissions(self.request, playlist)
            return playlist
        except PermissionDenied:
            return Response({"detail": "not enough permissions"}, status=status.HTTP_400_BAD_REQUEST)
        except BaseException:
            return Response(
                {"detail": "Playlist does not exist"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @swagger_auto_schema(
        manual_parameters=[],
        tags=['Playlists'],
        operation_summary='to_be_written',
        operation_description='to_be_written',
    )
    def get(self, request, friendly_token, format=None):
        playlist = self.get_playlist(friendly_token)
        if isinstance(playlist, Response):
            return playlist

        serializer = PlaylistDetailSerializer(playlist, context={"request": request})

        base_qs = PlaylistMedia.objects.filter(playlist=playlist)
        if is_mediacms_editor(request.user):
            playlist_media = base_qs
        else:
            accessible = Q(media__state__in=["public", "unlisted"])
            if request.user.is_authenticated:
                accessible |= Q(media__user=request.user) | Q(media__permissions__user=request.user)
                if getattr(settings, 'USE_RBAC', False):
                    accessible |= Q(media__category__in=request.user.get_rbac_categories_as_member())
            playlist_media = base_qs.filter(accessible)

        playlist_media = playlist_media.prefetch_related("media__user").select_related("media").distinct()

        playlist_media = [c.media for c in playlist_media]

        playlist_media_serializer = MediaSerializer(playlist_media, many=True, context={"request": request})
        ret = serializer.data
        ret["playlist_media"] = playlist_media_serializer.data

        return Response(ret)

    @swagger_auto_schema(
        manual_parameters=[],
        tags=['Playlists'],
        operation_summary='to_be_written',
        operation_description='to_be_written',
    )
    def post(self, request, friendly_token, format=None):
        playlist = self.get_playlist(friendly_token)
        if isinstance(playlist, Response):
            return playlist
        serializer = PlaylistDetailSerializer(playlist, data=request.data, context={"request": request})
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @swagger_auto_schema(
        manual_parameters=[],
        tags=['Playlists'],
        operation_summary='to_be_written',
        operation_description='Add, remove or reorder media. For add, dry_run=true validates capacity without writing. Capacity failures return HTTP 400 with code=playlist_capacity_exceeded, limit and playlists.',
        request_body=openapi.Schema(type=openapi.TYPE_OBJECT, properties={
            'type': openapi.Schema(type=openapi.TYPE_STRING, enum=['add', 'remove', 'ordering']),
            'media_friendly_token': openapi.Schema(type=openapi.TYPE_STRING),
            'ordering': openapi.Schema(type=openapi.TYPE_INTEGER),
            'dry_run': openapi.Schema(type=openapi.TYPE_BOOLEAN, default=False),
        }),
    )
    def put(self, request, friendly_token, format=None):
        playlist = self.get_playlist(friendly_token)
        if isinstance(playlist, Response):
            return playlist
        action = request.data.get("type")
        media_friendly_token = request.data.get("media_friendly_token")
        ordering = 0
        if request.data.get("ordering"):
            try:
                ordering = int(request.data.get("ordering"))
            except ValueError:
                pass

        if action in ["add", "remove", "ordering"]:
            media = Media.objects.filter(friendly_token=media_friendly_token).first()
            if media:
                if action == "add":
                    if media.state == "private" and not (request.user.has_member_access_to_media(media) or is_mediacms_editor(request.user)):
                        return Response({"detail": "media is not valid"}, status=status.HTTP_400_BAD_REQUEST)
                    dry_run = request.data.get("dry_run", False)
                    if not isinstance(dry_run, bool):
                        return Response({"detail": "dry_run must be a boolean"}, status=status.HTTP_400_BAD_REQUEST)
                    try:
                        result = add_media_to_playlists(playlist_ids=[playlist.pk], media_ids=[media.pk], dry_run=dry_run)
                    except PlaylistCapacityExceeded as exc:
                        return Response(exc.payload, status=status.HTTP_400_BAD_REQUEST)
                    except PlaylistUnavailable as exc:
                        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
                    if dry_run:
                        return Response(result)
                    return Response({"detail": "media added to Playlist", **result}, status=status.HTTP_201_CREATED)
                elif action == "remove":
                    PlaylistMedia.objects.filter(playlist=playlist, media=media).delete()
                    return Response(
                        {"detail": "media removed from Playlist"},
                        status=status.HTTP_201_CREATED,
                    )
                elif action == "ordering":
                    if ordering:
                        playlist.set_ordering(media, ordering)
                        return Response(
                            {"detail": "new ordering set"},
                            status=status.HTTP_201_CREATED,
                        )
            else:
                return Response({"detail": "media is not valid"}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {"detail": "invalid or not specified action"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @swagger_auto_schema(
        manual_parameters=[],
        tags=['Playlists'],
        operation_summary='to_be_written',
        operation_description='to_be_written',
    )
    def delete(self, request, friendly_token, format=None):
        playlist = self.get_playlist(friendly_token)
        if isinstance(playlist, Response):
            return playlist

        playlist.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
