from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Event
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import RequestFactory, SimpleTestCase
from PIL import Image

from projects.image_previews import private_image_response, render_preview


class MediaCapacityTests(SimpleTestCase):
    def image_file(self, size=(20, 10), format='PNG', **kwargs):
        output = BytesIO()
        with Image.new('RGB', size, 'red') as image:
            image.save(output, format=format, **kwargs)
        return ContentFile(output.getvalue(), name='capacity-test.' + format.lower())

    def test_large_png_falls_back_before_decoding_and_preserves_original(self):
        cache.clear()
        file = self.image_file((2500, 2000))
        original = file.read()
        file.seek(0)
        with patch('PIL.PngImagePlugin.PngImageFile.load', side_effect=AssertionError('Must not decode')):
            response = private_image_response(RequestFactory().get('/', {'thumb': '1'}), file)
        self.assertTrue(response.streaming)
        self.assertEqual(b''.join(response.streaming_content), original)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        response.close()
        self.assertTrue(response.closed)

    def test_large_jpeg_is_reduced_before_pixel_decode(self):
        file = self.image_file((6000, 3000), format='JPEG')
        from PIL.JpegImagePlugin import JpegImageFile
        original_load = JpegImageFile.load
        decoded_sizes = []

        def load(image):
            decoded_sizes.append(image.size)
            return original_load(image)

        with patch.object(JpegImageFile, 'load', load):
            data = render_preview(file, 480)
        self.assertTrue(decoded_sizes)
        self.assertLessEqual(max(w * h for w, h in decoded_sizes), 4_000_000)
        with Image.open(BytesIO(data)) as result:
            self.assertEqual(result.size, (480, 240))

    def test_only_one_decoder_runs_and_slot_is_released_after_failure(self):
        entered, release = Event(), Event()

        def wait_then_fail(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError('Test did not release decoder')
            raise OSError('Synthetic read failure')

        first, second = Mock(), Mock()
        first.open.side_effect = wait_then_fail
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(render_preview, first, 480)
            try:
                self.assertTrue(entered.wait(5))
                self.assertIsNone(render_preview(second, 480))
                second.open.assert_not_called()
            finally:
                release.set()
            with self.assertRaises(OSError):
                future.result()
        self.assertIsNotNone(render_preview(self.image_file(), 480))

    def test_orientation_is_preserved_after_resize(self):
        exif = Image.Exif()
        exif[274] = 6
        data = render_preview(self.image_file(format='JPEG', exif=exif), 480)
        with Image.open(BytesIO(data)) as result:
            self.assertEqual(result.size, (10, 20))
