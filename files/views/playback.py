from decimal import Decimal, InvalidOperation

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from files.models import Media, MediaAssetVersion, MediaPlaybackProgress


class PlaybackProgressView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, media_id):
        progress = MediaPlaybackProgress.objects.filter(media_id=media_id, user=request.user).select_related("asset_version").first()
        if progress is None:
            return Response({"position_seconds": 0, "completed": False, "asset_version_id": None})
        return Response({"position_seconds": progress.position_seconds, "duration_seconds": progress.duration_seconds, "completed": progress.completed, "asset_version_id": str(progress.asset_version_id) if progress.asset_version_id else None})

    def put(self, request, media_id):
        if not Media.objects.filter(pk=media_id).exists():
            return Response({"detail": "Media not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            position = Decimal(str(request.data.get("position_seconds", 0)))
            duration_value = request.data.get("duration_seconds")
            duration = Decimal(str(duration_value)) if duration_value is not None else None
        except (InvalidOperation, TypeError, ValueError):
            return Response({"detail": "Playback position is invalid."}, status=status.HTTP_400_BAD_REQUEST)
        if position < 0 or (duration is not None and (duration < 0 or position > duration + Decimal("1"))):
            return Response({"detail": "Playback position is outside the media duration."}, status=status.HTTP_400_BAD_REQUEST)
        asset_version_id = request.data.get("asset_version_id") or None
        if asset_version_id is not None:
            try:
                belongs_to_media = MediaAssetVersion.objects.filter(pk=asset_version_id, media_id=media_id).exists()
            except (TypeError, ValueError):
                belongs_to_media = False
            if not belongs_to_media:
                return Response({"detail": "Asset version does not belong to this media."}, status=status.HTTP_400_BAD_REQUEST)
        progress, _ = MediaPlaybackProgress.objects.update_or_create(
            media_id=media_id,
            user=request.user,
            defaults={
                "position_seconds": position,
                "duration_seconds": duration,
                "completed": bool(request.data.get("completed", False)),
                "asset_version_id": asset_version_id,
            },
        )
        return Response({"position_seconds": progress.position_seconds, "completed": progress.completed, "asset_version_id": str(progress.asset_version_id) if progress.asset_version_id else None})
