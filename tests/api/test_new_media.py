import uuid

from django.test import Client, TestCase

from files.models import Media
from files.tests import create_account

API_V1_LOGIN_URL = '/api/v1/login'


class TestX(TestCase):
    fixtures = ["fixtures/categories.json", "fixtures/encoding_profiles.json"]

    def setUp(self):
        self.password = 'this_is_a_fake_password'

        self.user = create_account(password=self.password)

    def test_file_upload(self):
        client = Client()
        client.login(username=self.user.username, password=self.password)

        # Exercise both legacy upload endpoints. Local FFmpeg transcoding is
        # intentionally disabled in the AWS-only deployment; MediaConvert
        # acceptance covers generated HLS outputs separately.
        with open('fixtures/small_video.mp4', 'rb') as fp:
            client.post('/api/v1/media', {'title': 'small video file test', 'media_file': fp})

        with open('fixtures/test_image.png', 'rb') as fp:
            client.post('/api/v1/media', {'title': 'image file test', 'media_file': fp})

        with open('fixtures/medium_video.mp4', 'rb') as fp:
            client.post('/fu/upload/', {'qqfile': fp, 'qqfilename': 'medium_video.mp4', 'qquuid': str(uuid.uuid4())})

        self.assertEqual(Media.objects.all().count(), 3, "Problem with file upload")
        # by default the portal_workflow is public, so anything uploaded gets public
        self.assertEqual(Media.objects.filter(state='public').count(), 3, "Expected all media to be public, as per the default portal workflow")
        self.assertEqual(Media.objects.filter(media_type='video').count(), 2, "Media identification failed")
        self.assertEqual(Media.objects.filter(media_type='image').count(), 1, "Media identification failed")
        self.assertEqual(Media.objects.filter(user=self.user).count(), 3, "User assignment failed")
