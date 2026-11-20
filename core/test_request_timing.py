import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.test import RequestFactory, SimpleTestCase

from config.runtime import database_runtime_settings
from core.request_timing import RequestTimingMiddleware


class RequestTimingTests(SimpleTestCase):
    def request(self, route='projects:selection_detail'):
        request = RequestFactory().post(
            '/private-client/123/?token=private-token',
            {'password': 'private-password', 'financial': 'private-amount'},
            HTTP_AUTHORIZATION='Bearer private-header',
        )
        request.resolver_match = SimpleNamespace(view_name=route, url_name='detail')
        return request

    def test_slow_response_contains_only_route_and_timing(self):
        middleware = RequestTimingMiddleware(lambda request: HttpResponse(b'private-body'))
        with patch('core.request_timing.perf_counter', side_effect=[10, 13]), self.assertLogs(
            'heritage.request_timing', level='WARNING',
        ) as captured:
            response = middleware(self.request())
        self.assertEqual(response.content, b'private-body')
        self.assertEqual(captured.records[0].getMessage(),
                         'request_timing route=projects:selection_detail method=POST status=200 duration_ms=3000 phase=response')
        self.assertIsNone(captured.records[0].exc_info)

    def test_fast_success_is_silent_but_server_failure_is_recorded(self):
        with patch('core.request_timing.perf_counter', return_value=1), patch(
            'core.request_timing.logger.warning',
        ) as log:
            RequestTimingMiddleware(lambda request: HttpResponse())(self.request())
            log.assert_not_called()
            RequestTimingMiddleware(lambda request: HttpResponse(status=503))(self.request())
            self.assertEqual(log.call_args.args[3], 503)

    def test_unmatched_request_never_logs_raw_path_or_nonstandard_method(self):
        request = self.request()
        request.resolver_match = None
        request.method = 'private-method'
        with patch('core.request_timing.perf_counter', return_value=1), self.assertLogs(
            'heritage.request_timing', level='WARNING',
        ) as captured:
            RequestTimingMiddleware(lambda request: HttpResponse(status=500))(request)
        self.assertIn('route=unmatched method=OTHER', captured.output[0])
        self.assertNotIn('private', captured.output[0])

    def test_health_warns_at_one_second(self):
        with patch('core.request_timing.perf_counter', side_effect=[1, 2.1]), self.assertLogs(
            'heritage.request_timing', level='WARNING',
        ) as captured:
            RequestTimingMiddleware(lambda request: HttpResponse())(self.request('core:health'))
        self.assertIn('duration_ms=1100', captured.output[0])

    def test_original_exception_is_preserved_without_its_private_message(self):
        failure = RuntimeError('private-exception')

        def fail(request):
            raise failure

        with self.assertLogs('heritage.request_timing', level='WARNING') as captured:
            with self.assertRaises(RuntimeError) as raised:
                RequestTimingMiddleware(fail)(self.request())
        self.assertIs(raised.exception, failure)
        self.assertNotIn('private-exception', str(captured.output))
        self.assertIsNone(captured.records[0].exc_info)

    def test_file_stream_keeps_bytes_headers_wrapper_and_closes_resources_once(self):
        file = BytesIO(b'private-document')
        response = FileResponse(file, as_attachment=True, filename='private-name.pdf')
        response['Cache-Control'] = 'private, no-store'
        with patch('core.request_timing.perf_counter', side_effect=[1, 1.1, 4]), self.assertLogs(
            'heritage.request_timing', level='WARNING',
        ) as captured:
            result = RequestTimingMiddleware(lambda request: response)(self.request())
            self.assertIs(result.file_to_stream, file)
            self.assertEqual(b''.join(result.streaming_content), b'private-document')
            self.assertEqual(result['Cache-Control'], 'private, no-store')
            self.assertEqual(result['Content-Length'], '16')
            result.close()
            result.close()
        self.assertTrue(file.closed)
        self.assertEqual(len(captured.records), 1)
        self.assertIn('phase=stream_close', captured.output[0])
        self.assertNotIn('private', captured.output[0])

    def test_stream_error_still_propagates_and_closes(self):
        closed = []

        def chunks():
            try:
                yield b'first'
                raise OSError('private-storage-error')
            finally:
                closed.append(True)

        response = StreamingHttpResponse(chunks())
        result = RequestTimingMiddleware(lambda request: response)(self.request())
        with self.assertRaisesMessage(OSError, 'private-storage-error'):
            list(result.streaming_content)
        result.close()
        self.assertEqual(closed, [True])

    def test_broken_logging_does_not_break_response_or_download_cleanup(self):
        file = BytesIO(b'example')
        with patch('core.request_timing.perf_counter', side_effect=[1, 4, 7]), patch(
            'core.request_timing.logger.warning', side_effect=OSError('closed log'),
        ):
            result = RequestTimingMiddleware(lambda request: FileResponse(file))(self.request())
            result.close()
        self.assertTrue(file.closed)

    def test_parallel_requests_do_not_share_route_or_status(self):
        barrier = Barrier(8)

        def respond(request):
            barrier.wait(timeout=5)
            return HttpResponse(status=request.expected_status)

        middleware = RequestTimingMiddleware(respond)

        def run(index):
            request = self.request(f'route:{index}')
            request.expected_status = 500 + index
            return middleware(request).status_code

        with self.assertLogs('heritage.request_timing', level='WARNING') as captured:
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(list(pool.map(run, range(8))), list(range(500, 508)))
        self.assertEqual(len(captured.records), 8)
        for record in captured.records:
            self.assertEqual(record.args[2], 500 + int(record.args[0].split(':')[1]))


class DatabaseRuntimeTests(SimpleTestCase):
    def test_actual_production_settings_preserve_limits_with_ssl_and_management(self):
        script = '''
import json
from config.settings import prod
db = prod.DATABASES['default']
print(json.dumps({
    'options': db['OPTIONS'], 'age': db['CONN_MAX_AGE'],
    'health': db['CONN_HEALTH_CHECKS'],
    'cache': prod.CACHES['default']['OPTIONS'],
    'timing': 'core.request_timing.RequestTimingMiddleware' in prod.MIDDLEWARE,
}))
'''
        for management in ('0', '1'):
            with self.subTest(management=management):
                environment = {
                    **os.environ, 'SECRET_KEY': 'test-only', 'DB_NAME': 'test',
                    'DB_USER': 'test', 'DB_PASSWORD': 'test',
                    'ALLOWED_HOSTS': 'testserver', 'PRIVATE_STORAGE_BACKEND': 'local',
                    'SENTRY_DSN': '', 'SENTRY_MANAGEMENT_COMMAND': management,
                    'DB_SSL_REQUIRE': 'true',
                }
                process = subprocess.run(
                    [sys.executable, '-c', script], env=environment,
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True, text=True, timeout=30, check=True,
                )
                config = json.loads(process.stdout)
                self.assertEqual(config['options']['sslmode'], 'require')
                self.assertEqual(config['options']['connect_timeout'], 3)
                self.assertEqual(config['age'], 60)
                self.assertTrue(config['health'])
                self.assertTrue(config['timing'])
                self.assertEqual(config['cache']['SOCKET_TIMEOUT'], 3)
                self.assertEqual(config['cache']['SOCKET_CONNECT_TIMEOUT'], 3)
                self.assertEqual('options' in config['options'], management == '0')

    def test_web_connections_preserve_ssl_and_bound_waits(self):
        original = {'OPTIONS': {'sslmode': 'require', 'options': '-c timezone=UTC'}}
        configured = database_runtime_settings(original)
        self.assertEqual(original, {'OPTIONS': {'sslmode': 'require', 'options': '-c timezone=UTC'}})
        self.assertEqual(configured['CONN_MAX_AGE'], 60)
        self.assertTrue(configured['CONN_HEALTH_CHECKS'])
        self.assertEqual(configured['OPTIONS']['connect_timeout'], 3)
        self.assertEqual(configured['OPTIONS']['sslmode'], 'require')
        self.assertEqual(configured['OPTIONS']['options'],
                         '-c timezone=UTC -c statement_timeout=15000 -c lock_timeout=5000')

    def test_migrations_and_imports_do_not_inherit_web_query_deadlines(self):
        configured = database_runtime_settings({'OPTIONS': {'sslmode': 'require'}}, management_command=True)
        self.assertNotIn('options', configured['OPTIONS'])
        self.assertEqual(configured['OPTIONS']['connect_timeout'], 3)
