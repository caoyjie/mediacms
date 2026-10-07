from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import connection, connections, close_old_connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient
from files.models import Playlist, PlaylistMedia
from users.models import User
from tests.api.test_playlist_capacity import make_media


@override_settings(MAX_MEDIA_PER_PLAYLIST=100)
class PlaylistConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, 'postgresql', 'These tests require real PostgreSQL locks')
        self.user = User.objects.create_user(username='concurrent-owner')
        self.media = make_media(self.user)
        self.playlist = Playlist.objects.create(user=self.user, title='Concurrent')
        PlaylistMedia.objects.bulk_create([PlaylistMedia(playlist=self.playlist, media=m, ordering=i+1)
                                          for i, m in enumerate(self.media[:99])])

    def race(self, tokens, targets=None):
        barrier = Barrier(2)
        def request(index):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(User.objects.get(pk=self.user.pk))
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                barrier.wait(timeout=5)
                if targets:
                    response = client.post('/api/v1/media/user/bulk_actions', dict(action='add_to_playlist',
                        playlist_ids=targets[index], media_ids=[tokens[index]]), format='json')
                elif index == 0:
                    response = client.put(f'/api/v1/playlists/{self.playlist.friendly_token}',
                        dict(type='add', media_friendly_token=tokens[index]), format='json')
                else:
                    response = client.post('/api/v1/media/user/bulk_actions', dict(action='add_to_playlist',
                        playlist_ids=[self.playlist.pk], media_ids=[tokens[index]]), format='json')
                return response.status_code, response.data
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(request, i) for i in range(2)]
            return [f.result(timeout=15) for f in futures]

    def test_mixed_endpoints_cannot_overfill(self):
        results = self.race([m.friendly_token for m in self.media[99:101]])
        self.assertEqual(sum(status == 400 for status, _ in results), 1, results)
        self.assertEqual(sum(status in [200, 201] for status, _ in results), 1, results)
        self.assertEqual(self.playlist.playlistmedia_set.count(), 100)
        self.assertEqual(next(data for status, data in results if status == 400)['code'], 'playlist_capacity_exceeded')

    def test_same_media_is_added_once(self):
        results = self.race([self.media[99].friendly_token] * 2)
        self.assertEqual(sorted(data['added_count'] for _, data in results), [0, 1])
        self.assertTrue(all(status in [200, 201] for status, _ in results))
        self.assertEqual(self.playlist.playlistmedia_set.count(), 100)

    def test_reverse_target_order_no_deadlock_or_partial_writes(self):
        empty = Playlist.objects.create(user=self.user, title='Empty')
        results = self.race([m.friendly_token for m in self.media[99:101]],
                            [[empty.pk, self.playlist.pk], [self.playlist.pk, empty.pk]])
        self.assertEqual(sorted(status for status, _ in results), [200, 400])
        self.assertEqual(empty.playlistmedia_set.count(), 1)
        self.assertEqual(self.playlist.playlistmedia_set.count(), 100)
        winner = empty.playlistmedia_set.get().media_id
        self.assertTrue(self.playlist.playlistmedia_set.filter(media_id=winner).exists())
