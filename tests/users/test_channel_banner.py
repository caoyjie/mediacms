from io import BytesIO
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from PIL import Image

from users.models import Channel


class ChannelBannerTests(SimpleTestCase):
    def save_banner(self, image):
        source = BytesIO()
        image.save(source, format="PNG")
        channel = Channel()
        channel.banner_logo.save("banner.png", SimpleUploadedFile("banner.png", source.getvalue(), content_type="image/png"), save=False)
        with channel.banner_logo.open("rb") as saved:
            with Image.open(saved) as result:
                return result.copy()

    def test_banner_keeps_resolution_and_only_crops_to_ratio(self):
        cases = [
            ((2700, 600), (2700, 600)),
            ((2700, 1000), (2700, 600)),
            ((3600, 600), (2700, 600)),
            ((450, 100), (450, 100)),
            ((1001, 223), (1001, 222)),
        ]
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            for original, expected in cases:
                with self.subTest(original=original):
                    result = self.save_banner(Image.new("RGB", original, "green"))
                    self.assertEqual(result.size, expected)

    def test_banner_crops_equally_from_opposite_edges(self):
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            for size, center in [((180, 80), (0, 20, 180, 60)), ((260, 40), (40, 0, 220, 40))]:
                with self.subTest(size=size):
                    image = Image.new("RGB", size, "red")
                    image.paste((0, 255, 0), center)
                    result = self.save_banner(image)
                    self.assertEqual(result.size, (180, 40))
                    # A centered crop contains only the green region; allow JPEG rounding.
                    self.assertLessEqual(result.getchannel("R").getextrema()[1], 2)
                    self.assertGreaterEqual(result.getchannel("G").getextrema()[0], 253)
