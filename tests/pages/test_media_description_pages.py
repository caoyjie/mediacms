import json
import re
from html.parser import HTMLParser
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase


class DescriptionMetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.description = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('name') == 'description':
            self.description = attrs.get('content')


class MediaDescriptionPageTests(SimpleTestCase):
    def test_description_jsonld_round_trip(self):
        source = '# Heading\n"quoted" \\ & <div>code</div>\n</script><script>alert(1)</script>\n'
        for media_type in ['video', 'audio', 'image', 'pdf']:
            with self.subTest(media_type=media_type):
                media = SimpleNamespace(title='Description', description=source, media_type=media_type,
                                        state='public', get_absolute_url='/view?m=test', duration=7200)
                rendered = render_to_string('cms/media.html', {'media_object': media})
                blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', rendered, re.S)
                self.assertEqual(len(blocks), 1)
                self.assertEqual(json.loads(blocks[0])['description'], source)
                self.assertNotIn('<script>alert(1)</script>', rendered)
                parser = DescriptionMetadataParser()
                parser.feed(rendered)
                self.assertEqual(parser.description, source)
