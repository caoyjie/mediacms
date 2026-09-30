import uuid
from unittest.mock import patch
from urllib.parse import quote

from django.test import TestCase
from rest_framework.test import APIClient

from files.models import Media, MediaPermission
from users.models import User


class PlaybackProgressTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='progress-viewer')
        self.other = User.objects.create_user(username='progress-owner')
        self.media = Media(
            user=self.other, title='Progress video', friendly_token='progress-video',
            media_file='original/progress.mp4', media_type='video', duration=1200, state='public',
        )
        Media.objects.bulk_create([self.media])
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.base = '/api/v1/media/progress-video/'
        self.sessions = {}

    def claim(self, session_id=None):
        session_id = session_id or str(uuid.uuid4())
        response = self.client.post(self.base + 'playback-session', {
            'session_id': session_id,
            'content_revision': str(getattr(self.media, 'content_revision', uuid.uuid4())),
        }, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        generation = response.json()['generation']
        self.sessions[generation] = session_id
        return generation

    def save_progress(self, generation, position=480, sequence=1, **extra):
        return self.client.put(self.base + 'playback-progress', {
            'generation': generation, 'sequence': sequence,
            'session_id': self.sessions[generation],
            'content_revision': str(self.media.content_revision),
            'position': position, 'duration': 1200, 'ended': False, **extra,
        }, format='json')

    def progress(self):
        response = self.client.get('/api/v1/playback-progress?media=progress-video')
        self.assertEqual(response.status_code, 200)
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        return response.json().get('progress-video')

    def test_round_trip_and_backward_seek(self):
        generation = self.claim()
        self.assertIsNone(self.progress())
        self.assertEqual(self.save_progress(generation).status_code, 200)
        self.assertEqual(self.progress()['position'], 480)
        self.assertEqual(self.save_progress(generation, position=120, sequence=2).status_code, 200)
        self.assertEqual(self.progress()['position'], 120)

    def test_old_session_and_delayed_save_cannot_overwrite(self):
        first = self.claim()
        self.assertEqual(self.save_progress(first, sequence=2).status_code, 200)
        self.assertEqual(self.save_progress(first, position=300, sequence=1).status_code, 409)
        second = self.claim()
        self.assertEqual(self.progress()['position'], 480)
        self.assertEqual(self.save_progress(second, position=60).status_code, 200)
        self.assertEqual(self.save_progress(first, sequence=3).status_code, 409)
        self.assertEqual(self.progress()['position'], 60)

    def test_claim_retry_does_not_take_ownership_back(self):
        session_id = str(uuid.uuid4())
        first = self.claim(session_id)
        self.assertEqual(self.claim(session_id), first)
        second = self.claim()
        self.assertEqual(self.claim(session_id), first)
        self.assertEqual(self.save_progress(second).status_code, 200)
        self.assertEqual(self.save_progress(first).status_code, 409)

    def test_completion_clamping_and_replay(self):
        generation = self.claim()
        self.assertEqual(self.save_progress(generation, position=1195).status_code, 200)
        self.assertTrue(self.progress()['completed'])
        generation = self.claim()
        self.assertTrue(self.progress()['completed'])
        self.save_progress(generation, position=10)
        self.assertFalse(self.progress()['completed'])
        self.save_progress(generation, position=1210, sequence=2)
        self.assertEqual(self.progress()['position'], 1200)
        self.save_progress(generation, position=1190, sequence=3, ended=True)
        self.assertTrue(self.progress()['completed'])

    def test_invalid_numbers_and_types_are_rejected(self):
        generation = self.claim()
        for values in [{'position': -1}, {'position': 'NaN'}, {'duration': 0}, {'duration': 'Infinity'},
                       {'sequence': 0}, {'position': True}, {'ended': 'yes'}, {'sequence': 1.5}]:
            with self.subTest(values=values):
                self.assertEqual(self.save_progress(generation, **values).status_code, 400)

    def test_account_isolation_and_private_media_access(self):
        generation = self.claim()
        self.save_progress(generation)
        self.client.force_authenticate(self.other)
        self.assertIsNone(self.progress())
        self.media.state = 'private'
        Media.objects.filter(pk=self.media.pk).update(state='private')
        self.client.force_authenticate(self.user)
        self.assertIsNone(self.progress())
        self.assertEqual(self.save_progress(generation).status_code, 404)
        self.client.force_authenticate(self.other)
        self.claim()

    def test_anonymous_and_csrf_requests_are_denied(self):
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get('/api/v1/playback-progress?media=progress-video').status_code, (401, 403))
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.base + 'playback-session', {}, format='json').status_code, 403)

    def test_revision_change_hides_progress_and_rejects_old_writer(self):
        generation = self.claim()
        self.save_progress(generation)
        Media.objects.filter(pk=self.media.pk).update(content_revision=uuid.uuid4())
        self.assertIsNone(self.progress())
        self.assertEqual(self.save_progress(generation).status_code, 409)

    def test_batch_limit_and_non_playable_media(self):
        self.assertEqual(self.client.get('/api/v1/playback-progress', {'media': ','.join(str(i) for i in range(101))}).status_code, 400)
        Media.objects.filter(pk=self.media.pk).update(media_type='image')
        self.assertEqual(self.client.post(self.base + 'playback-session', {
            'session_id': str(uuid.uuid4()), 'content_revision': str(getattr(self.media, 'content_revision', uuid.uuid4())),
        }, format='json').status_code, 400)

    def test_old_tab_cannot_save_into_newly_logged_in_account(self):
        generation = self.claim()
        old_session = self.sessions[generation]
        self.client.force_authenticate(self.other)
        new_generation = self.claim()
        self.assertEqual(self.save_progress(new_generation, position=60).status_code, 200)
        self.assertEqual(self.save_progress(generation, sequence=2, session_id=old_session).status_code, 409)
        self.assertEqual(self.progress()['position'], 60)

    def test_old_page_cannot_claim_after_account_switch(self):
        self.client.force_authenticate(self.other)
        response = self.client.post(self.base + 'playback-session', {
            'session_id': str(uuid.uuid4()), 'content_revision': str(self.media.content_revision),
        }, format='json', HTTP_X_PLAYBACK_USER=self.user.username)
        self.assertEqual(response.status_code, 403)

    def test_unicode_account_guard(self):
        self.user.username = '观众'
        self.user.save(update_fields=['username'])
        response = self.client.post(self.base + 'playback-session', {
            'session_id': str(uuid.uuid4()), 'content_revision': str(self.media.content_revision),
        }, format='json', HTTP_X_PLAYBACK_USER=quote(self.user.username))
        self.assertEqual(response.status_code, 200)

    def test_unlisted_and_explicitly_shared_media_are_allowed(self):
        Media.objects.filter(pk=self.media.pk).update(state='unlisted')
        self.claim()
        Media.objects.filter(pk=self.media.pk).update(state='private')
        MediaPermission.objects.create(user=self.user, owner_user=self.other, media=self.media, permission='viewer')
        generation = self.claim()
        self.assertEqual(self.save_progress(generation).status_code, 200)
        self.assertEqual(self.progress()['position'], 480)

    def test_source_replacement_invalidates_but_title_edits_preserve_revision(self):
        self.media.title = 'Edited title'
        self.media.save(update_fields=['title'])
        self.media.refresh_from_db()
        original = self.media.content_revision
        generation = self.claim()
        self.save_progress(generation)
        self.media.media_file = 'original/replacement.mp4'
        # Only the asynchronous media processing is outside this test's boundary.
        with patch('files.tasks.media_init.apply_async'):
            self.media.save(update_fields=['media_file'])
        self.media.refresh_from_db()
        self.assertNotEqual(self.media.content_revision, original)
        self.assertIsNone(self.progress())

    def test_in_place_trim_invalidates_resume(self):
        from files.tasks import pre_trim_video_actions
        original = self.media.content_revision
        generation = self.claim()
        self.save_progress(generation)
        pre_trim_video_actions(self.media)
        self.media.refresh_from_db()
        self.assertNotEqual(self.media.content_revision, original)
        self.assertIsNone(self.progress())
