from html import unescape
import re

from django.test import TestCase
from django.utils import timezone

from files.forms import MediaMetadataForm
from files.models import Media
from users.models import User


class MediaDescriptionFormTests(TestCase):
    def test_description_form_round_trip(self):
        user = User.objects.create_user(username='description-form-owner')
        media = Media(user=user, title='Description', friendly_token='description-form',
                      media_file='original/description.png', media_type='image', state='public', add_date=timezone.now())
        Media.objects.bulk_create([media])
        source = '    <div>example</div>\nline  \n</textarea><script>alert(1)</script>\n'
        form = MediaMetadataForm(user, data={
            'title': 'Description', 'description': source, 'new_tags': '',
            'add_date': media.add_date.isoformat(), 'enable_comments': True,
        }, instance=media)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        media.refresh_from_db()
        self.assertEqual(media.description, source)
        editor = str(MediaMetadataForm(user, instance=media)['description'])
        self.assertNotIn('<script>', editor)
        self.assertEqual(editor.count('</textarea>'), 1)
        self.assertEqual(unescape(re.search(r'<textarea[^>]*>\n?(.*?)</textarea>', editor, re.S)[1]), source)
