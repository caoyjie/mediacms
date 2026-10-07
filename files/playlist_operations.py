"""Atomic, capacity-checked playlist additions. Callers must authorize all IDs."""
from collections import defaultdict

from django.conf import settings
from django.db import transaction

from .models import Playlist, PlaylistMedia


class PlaylistCapacityExceeded(Exception):
    def __init__(self, payload):
        self.payload = payload
        super().__init__(payload['detail'])


class PlaylistUnavailable(Exception):
    pass


@transaction.atomic
def add_media_to_playlists(*, playlist_ids: list[int], media_ids: list[int], dry_run: bool = False) -> dict:
    # Lock parents (including empty playlists), in a stable order across requests.
    ids = set(playlist_ids)
    playlists = list(Playlist.objects.filter(pk__in=ids).order_by('pk').select_for_update())
    if len(playlists) != len(ids):
        raise PlaylistUnavailable('Playlist no longer available')
    media_ids = list(dict.fromkeys(media_ids))
    existing = defaultdict(list)
    for playlist_id, media_id, ordering in PlaylistMedia.objects.filter(playlist_id__in=ids).values_list('playlist_id', 'media_id', 'ordering'):
        existing[playlist_id].append((media_id, ordering))

    limit = settings.MAX_MEDIA_PER_PLAYLIST
    summaries, pending, overflow = [], [], []
    for playlist in playlists:
        rows = existing[playlist.pk]
        present = {media_id for media_id, _ in rows}
        new_ids = [media_id for media_id in media_ids if media_id not in present]
        summary = dict(id=playlist.pk, title=playlist.title, current_count=len(rows),
                       requested_new_count=len(new_ids), remaining_capacity=max(limit - len(rows), 0))
        summaries.append(summary)
        if new_ids and len(rows) + len(new_ids) > limit:
            overflow.append(summary)
        ordering = max((order for _, order in rows), default=0)
        pending.extend(PlaylistMedia(playlist=playlist, media_id=media_id, ordering=ordering + i + 1)
                       for i, media_id in enumerate(new_ids))
    if overflow:
        raise PlaylistCapacityExceeded(dict(code='playlist_capacity_exceeded', detail='Playlist capacity exceeded',
                                            limit=limit, playlists=overflow))
    if dry_run:
        return dict(allowed=True, limit=limit, playlists=summaries)
    PlaylistMedia.objects.bulk_create(pending)
    for summary in summaries:
        summary['added_count'] = summary['requested_new_count']
    return dict(added_count=len(pending), limit=limit, playlists=summaries)
