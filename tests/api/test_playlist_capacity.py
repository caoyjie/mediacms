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


from rest_framework.test import APIClient
from unittest.mock import patch


@override_settings(MAX_MEDIA_PER_PLAYLIST=2)
class PlaylistCapacityAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='api-owner')
        self.other = User.objects.create_user(username='other-owner')
        self.media = make_media(self.user, 4)
        self.playlist = Playlist.objects.create(user=self.user, title='API Course')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def request(self, bulk=False, **extra):
        if bulk:
            return self.client.post('/api/v1/media/user/bulk_actions', dict(
                action='add_to_playlist', playlist_ids=[self.playlist.pk], media_ids=[self.media[0].friendly_token], **extra), format='json')
        return self.client.put(f'/api/v1/playlists/{self.playlist.friendly_token}', dict(
            type='add', media_friendly_token=self.media[0].friendly_token, **extra), format='json')

    def test_both_endpoints_preflight_and_idempotent_commit(self):
        for bulk in [False, True]:
            PlaylistMedia.objects.all().delete()
            response = self.request(bulk, dry_run=True)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertTrue(response.data['allowed'])
            self.assertEqual(PlaylistMedia.objects.count(), 0)
            for expected_added in [1, 0]:
                response = self.request(bulk)
                self.assertEqual(response.status_code, 200 if bulk else 201)
                self.assertEqual(response.data['added_count'], expected_added)
            self.assertEqual(PlaylistMedia.objects.count(), 1)

    def test_both_endpoints_structured_overflow_and_stale_preflight(self):
        for bulk in [False, True]:
            PlaylistMedia.objects.all().delete()
            self.assertEqual(self.request(bulk, dry_run=True).status_code, 200)
            PlaylistMedia.objects.bulk_create([PlaylistMedia(playlist=self.playlist, media=m) for m in self.media[1:3]])
            for dry_run in [True, False]:
                response = self.request(bulk, dry_run=dry_run)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data['code'], 'playlist_capacity_exceeded')
                self.assertEqual(response.data['limit'], 2)
                self.assertEqual(response.data['playlists'][0]['current_count'], 2)
                self.assertEqual(response.data['playlists'][0]['requested_new_count'], 1)
                self.assertEqual(response.data['playlists'][0]['remaining_capacity'], 0)
            self.assertEqual(PlaylistMedia.objects.count(), 2)

    def test_invalid_dry_run_never_writes(self):
        for bulk in [False, True]:
            for value in ['false', 1, None, []]:
                self.assertEqual(self.request(bulk, dry_run=value).status_code, 400)
        self.assertEqual(PlaylistMedia.objects.count(), 0)

    def test_bulk_rejects_whole_batch(self):
        empty = Playlist.objects.create(user=self.user, title='Empty')
        PlaylistMedia.objects.bulk_create([PlaylistMedia(playlist=self.playlist, media=m) for m in self.media[1:3]])
        response = self.client.post('/api/v1/media/user/bulk_actions', dict(action='add_to_playlist',
            playlist_ids=[empty.pk, self.playlist.pk], media_ids=[self.media[0].friendly_token]), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(empty.playlistmedia_set.count(), 0)

    def test_permissions_apply_to_preflight_and_commit(self):
        for dry_run in [True, False]:
            for bulk in [False, True]:
                for user in [None, self.other]:
                    self.client.force_authenticate(user)
                    response = self.request(bulk, dry_run=dry_run)
                    self.assertGreaterEqual(response.status_code, 400)
                    self.assertNotIn('playlists', response.data)
        self.assertEqual(PlaylistMedia.objects.count(), 0)

    def test_private_media_denied(self):
        Media.objects.filter(pk=self.media[0].pk).update(user=self.other, state='private')
        for dry_run in [True, False]:
            for bulk in [False, True]:
                self.assertGreaterEqual(self.request(bulk, dry_run=dry_run).status_code, 400)
        self.assertEqual(PlaylistMedia.objects.count(), 0)

    def test_deleted_after_authorization_is_controlled_failure(self):
        for bulk, target in [(False, 'files.views.playlists'), (True, 'files.views.media')]:
            with patch(target + '.add_media_to_playlists', side_effect=PlaylistUnavailable('Playlist no longer available')):
                self.assertEqual(self.request(bulk).status_code, 400)

    def test_bulk_target_deleted_after_queryset_authorization_aborts_every_add(self):
        from django.db.models.query import QuerySet
        empty = Playlist.objects.create(user=self.user, title='Must stay empty')
        victim_id = self.playlist.pk
        original_fetch = QuerySet._fetch_all
        deleted = False

        def delete_after_authorized_fetch(queryset):
            nonlocal deleted
            original_fetch(queryset)
            if not deleted and queryset.model is Playlist and queryset._result_cache and any(
                isinstance(p, Playlist) and p.pk == victim_id for p in queryset._result_cache
            ):
                deleted = True
                Playlist.objects.filter(pk=victim_id).delete()

        with patch.object(QuerySet, '_fetch_all', delete_after_authorized_fetch):
            response = self.client.post('/api/v1/media/user/bulk_actions', dict(action='add_to_playlist',
                playlist_ids=[victim_id, empty.pk], media_ids=[self.media[0].friendly_token]), format='json')
        self.assertTrue(deleted)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(empty.playlistmedia_set.count(), 0)

    def test_capacity_messages_are_in_frontend_translation_dictionary(self):
        from files.frontend_translations import get_translation
        for language, expected in [('zh-hans', '播放列表容量不足'), ('zh-hant', '播放清單容量不足')]:
            self.assertEqual(get_translation(language).get('Playlist capacity exceeded'), expected)
            self.assertIn('{count}', get_translation(language)['Limit: {count}'])
