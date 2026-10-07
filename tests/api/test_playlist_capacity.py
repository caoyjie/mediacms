from django.test import TestCase, override_settings
from files.models import Media, Playlist, PlaylistMedia
from files.playlist_operations import add_media_to_playlists, PlaylistCapacityExceeded, PlaylistUnavailable
from users.models import User


def make_media(user, count=102):
    return Media.objects.bulk_create([
        Media(user=user, title=f'Media {i}', friendly_token=f'capacity-{i}',
              media_file=f'original/capacity-{i}.mp4', media_type='video', state='public', listable=False)
        for i in range(count)
    ])


@override_settings(MAX_MEDIA_PER_PLAYLIST=100)
class PlaylistCapacityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='capacity-owner')
        self.media = make_media(self.user)
        self.playlist = Playlist.objects.create(user=self.user, title='Course')

    def fill(self, count):
        PlaylistMedia.objects.bulk_create([
            PlaylistMedia(playlist=self.playlist, media=m, ordering=i + 1)
            for i, m in enumerate(self.media[:count])
        ])

    def add(self, media, **kwargs):
        return add_media_to_playlists(playlist_ids=[self.playlist.pk], media_ids=[m.pk for m in media], **kwargs)

    def test_exact_boundary_and_duplicate_at_capacity(self):
        self.fill(99)
        self.assertEqual(self.add([self.media[99]])['added_count'], 1)
        self.assertEqual(self.add([self.media[99]])['added_count'], 0)
        self.assertEqual(self.playlist.playlistmedia_set.count(), 100)

    def test_overflow_returns_counts_and_writes_nothing(self):
        self.fill(99)
        with self.assertRaises(PlaylistCapacityExceeded) as caught:
            self.add(self.media[99:101])
        self.assertEqual(caught.exception.payload['code'], 'playlist_capacity_exceeded')
        self.assertEqual(caught.exception.payload['playlists'], [dict(
            id=self.playlist.pk, title='Course', current_count=99,
            requested_new_count=2, remaining_capacity=1)])
        self.assertEqual(self.playlist.playlistmedia_set.count(), 99)

    def test_multiple_targets_are_atomic(self):
        self.fill(100)
        empty = Playlist.objects.create(user=self.user, title='Empty')
        with self.assertRaises(PlaylistCapacityExceeded):
            add_media_to_playlists(playlist_ids=[empty.pk, self.playlist.pk], media_ids=[self.media[100].pk])
        self.assertEqual(empty.playlistmedia_set.count(), 0)
        self.assertEqual(self.playlist.playlistmedia_set.count(), 100)

    def test_dry_run_does_not_write_and_input_is_deduplicated(self):
        result = self.add([self.media[0], self.media[0]], dry_run=True)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['playlists'][0]['requested_new_count'], 1)
        self.assertEqual(self.playlist.playlistmedia_set.count(), 0)
        self.assertEqual(self.add([self.media[0], self.media[0]])['added_count'], 1)

    @override_settings(MAX_MEDIA_PER_PLAYLIST=2)
    def test_hidden_media_counts_and_limit_is_dynamic(self):
        self.fill(2)
        self.assertEqual(self.playlist.media_count, 0)
        with self.assertRaises(PlaylistCapacityExceeded) as caught:
            self.add([self.media[2]])
        self.assertEqual(caught.exception.payload['limit'], 2)

    def test_existing_duplicates_remain_and_order_uses_maximum(self):
        PlaylistMedia.objects.create(playlist=self.playlist, media=self.media[0], ordering=10)
        PlaylistMedia.objects.create(playlist=self.playlist, media=self.media[0], ordering=30)
        self.assertEqual(self.add(self.media[:2])['added_count'], 1)
        self.assertEqual(self.playlist.playlistmedia_set.count(), 3)
        self.assertEqual(self.playlist.playlistmedia_set.get(media=self.media[1]).ordering, 31)

    @override_settings(MAX_MEDIA_PER_PLAYLIST=1)
    def test_legacy_overflow_allows_noop_only(self):
        self.fill(2)
        self.assertEqual(self.add([self.media[0]])['added_count'], 0)
        with self.assertRaises(PlaylistCapacityExceeded):
            self.add([self.media[2]])

    def test_deleted_target_is_not_silently_skipped(self):
        pk = self.playlist.pk
        self.playlist.delete()
        with self.assertRaises(PlaylistUnavailable):
            add_media_to_playlists(playlist_ids=[pk], media_ids=[self.media[0].pk])
