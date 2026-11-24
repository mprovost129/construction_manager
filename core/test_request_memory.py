from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.http import FileResponse
from django.test import RequestFactory, SimpleTestCase

from core.request_memory import RequestMemoryMiddleware, memory_snapshot


class RequestMemoryTests(SimpleTestCase):
    def test_starts_before_view_and_reports_stream_close_without_private_data(self):
        request = RequestFactory().get('/private-name/?token=secret')
        request.resolver_match = SimpleNamespace(namespace='projects', url_name='document_download', view_name='projects:document_download')
        file = BytesIO(b'private PDF')
        response = FileResponse(file, filename='private.pdf')
        middleware = RequestMemoryMiddleware(lambda req: response)
        with patch('core.request_memory.memory_snapshot', return_value=(120000, 250000)), self.assertLogs('heritage.request_memory') as logs:
            middleware.process_view(request, None, (), {})
            result = middleware(request)
            self.assertIs(result.file_to_stream, file)
            self.assertEqual(b''.join(result.streaming_content), b'private PDF')
            result.close()
            result.close()
        self.assertTrue(file.closed)
        self.assertEqual(len(logs.records), 3)
        self.assertIn('phase=start', logs.output[0])
        self.assertIn('phase=stream_close', logs.output[-1])
        self.assertNotIn('private', str(logs.output))
        self.assertNotIn('secret', str(logs.output))

    def test_unavailable_os_metrics_are_nonfatal(self):
        with patch('core.request_memory.Path.read_text', side_effect=OSError):
            self.assertEqual(memory_snapshot(), (-1, -1))
