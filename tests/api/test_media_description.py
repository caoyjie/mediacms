from django.test import TestCase
from rest_framework.test import APIClient

from files.models import Media
from users.models import User


SOURCE = '    indented <div>example</div>\n\n# Heading\nline  \nnext\n```html\n<span>& \\ "quoted"</span>\n```\n'


class MediaDescriptionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='description-owner')
        self.media = Media(user=self.user, title='Description', friendly_token='description-test',
                           media_file='original/description.mp4', media_type='video', state='public', duration=7200)
        Media.objects.bulk_create([self.media])
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_description_model_round_trip(self):
        self.media.description = SOURCE
        self.media.title = '<b>Description</b>'
        self.media.save()
        self.media.refresh_from_db()
        self.assertEqual(self.media.description, SOURCE)
        self.assertEqual(self.media.title, 'Description')

    def test_description_api_round_trip(self):
        for source in [SOURCE, '  \n', '']:
            with self.subTest(source=source):
                response = self.client.put('/api/v1/media/description-test',
                                           {'title': 'Description', 'description': source}, format='multipart')
                self.assertEqual(response.status_code, 201, response.content)
                self.assertEqual(response.json()['description'], source)
                self.media.refresh_from_db()
                self.assertEqual(self.media.description, source)
                response = self.client.get('/api/v1/media/description-test')
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()['description'], source)
