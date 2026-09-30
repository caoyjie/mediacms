"""Account playback progress. Neither writes nor reads grant media access."""

import math
from urllib.parse import unquote

from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, serializers
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from actions.models import PlaybackProgress, PlaybackSession
from files.methods import is_mediacms_editor
from files.models import Media


def can_view(user, media):
    return media.state in ('public', 'unlisted') or is_mediacms_editor(user) or user.has_member_access_to_media(media)


def serialize_progress(progress):
    return {
        'position': progress.position, 'duration': progress.duration,
        'completed': progress.completed, 'content_revision': str(progress.content_revision),
        'updated_at': progress.updated_at.isoformat(),
    }


class ClaimInput(serializers.Serializer):
    session_id = serializers.UUIDField()
    content_revision = serializers.UUIDField()


class SaveInput(serializers.Serializer):
    session_id = serializers.UUIDField()
    content_revision = serializers.UUIDField()
    generation = serializers.IntegerField(min_value=1, max_value=2**53 - 1)
    sequence = serializers.IntegerField(min_value=1, max_value=2**53 - 1)
    position = serializers.FloatField(min_value=0)
    duration = serializers.FloatField(min_value=0.001)
    ended = serializers.BooleanField(default=False)

    def validate(self, data):
        for key in ('position', 'duration'):
            if isinstance(self.initial_data[key], bool) or not math.isfinite(data[key]):
                raise ValidationError({key: 'Expected a finite number.'})
        if 'ended' in self.initial_data and not isinstance(self.initial_data['ended'], bool):
            raise ValidationError({'ended': 'Expected a boolean.'})
        return data


class ProgressView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        # This is an account-switch guard, never a source of authentication.
        expected_user = request.headers.get('X-Playback-User')
        if expected_user is not None and unquote(expected_user) != request.user.username:
            raise PermissionDenied('The signed-in account changed. Reload the page.')

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response

    def media(self, token):
        media = get_object_or_404(Media.objects.select_for_update(), friendly_token=token)
        if not can_view(self.request.user, media):
            raise NotFound()
        if media.media_type not in ('video', 'audio') or media.duration <= 0:
            raise ValidationError('Media must be finite video or audio.')
        return media


class PlaybackProgressList(ProgressView):
    def get(self, request):
        tokens = list(dict.fromkeys(filter(None, request.query_params.get('media', '').split(','))))
        if len(tokens) > 100:
            raise ValidationError('At most 100 media tokens per request.')
        rows = PlaybackProgress.objects.filter(
            user=request.user, media__friendly_token__in=tokens,
            content_revision=F('media__content_revision'), updated_at__isnull=False,
            media__media_type__in=('video', 'audio'), media__duration__gt=0,
        ).select_related('media')
        return Response({row.media.friendly_token: serialize_progress(row) for row in rows if can_view(request.user, row.media)})


class PlaybackSessionClaim(ProgressView):
    @transaction.atomic
    def post(self, request, friendly_token):
        payload = ClaimInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        media = self.media(friendly_token)
        # The media lock also serializes first-ever claims for a user/media pair.
        if media.content_revision != data['content_revision']:
            return Response({'detail': 'Media content changed.'}, status=409)
        progress, _ = PlaybackProgress.objects.get_or_create(
            user=request.user, media=media, defaults={'content_revision': media.content_revision},
        )
        receipt = progress.sessions.filter(session_id=data['session_id']).first()
        if receipt:
            return Response({'generation': receipt.generation})
        if progress.content_revision != media.content_revision:
            progress.content_revision = media.content_revision
            progress.position = progress.duration = 0
            progress.completed = False
            progress.updated_at = None
        progress.generation += 1
        progress.sequence = 0
        progress.save()
        PlaybackSession.objects.create(progress=progress, session_id=data['session_id'], generation=progress.generation)
        return Response({'generation': progress.generation})


class PlaybackProgressSave(ProgressView):
    @transaction.atomic
    def put(self, request, friendly_token):
        payload = SaveInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        media = self.media(friendly_token)
        progress = PlaybackProgress.objects.filter(user=request.user, media=media).first()
        if (not progress or media.content_revision != data['content_revision']
                or progress.content_revision != media.content_revision or progress.generation != data['generation']
                or data['sequence'] <= progress.sequence
                or not progress.sessions.filter(session_id=data['session_id'], generation=data['generation']).exists()):
            return Response({'detail': 'Progress session, revision, or sequence is stale.'}, status=409)
        progress.position = min(data['position'], media.duration)
        progress.duration = media.duration
        progress.completed = data['ended'] or progress.position >= max(0, media.duration - 5)
        progress.updated_at = timezone.now()
        progress.sequence = data['sequence']
        progress.save()
        return Response(serialize_progress(progress))
