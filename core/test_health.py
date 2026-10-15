from unittest.mock import patch

from django.core.cache import cache
from django.db import OperationalError
from django.test import TestCase, override_settings
from django.urls import reverse


class HealthProbeTests(TestCase):
    def test_liveness_works_without_database_or_cache(self):
        with patch('core.health.connection.cursor', side_effect=OperationalError('private connection detail')):
            with patch('core.health.cache') as cache_backend:
                response = self.client.get(reverse('core:health_live'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        cache_backend.set.assert_not_called()
        cache_backend.get.assert_not_called()
        self.assertNotIn('sessionid', response.cookies)

    def test_readiness_checks_database_and_cache_without_login(self):
        with self.assertNumQueries(1) as queries:
            response = self.client.get(reverse('core:health_ready'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertEqual(queries.captured_queries[0]['sql'], 'SELECT 1')
        self.assertNotIn('sessionid', response.cookies)

    def test_database_failure_returns_503_without_private_details_or_cache_calls(self):
        secret = 'password=private-secret host=private-database'
        with patch('core.health.connection.cursor', side_effect=OperationalError(secret)):
            with patch('core.health.cache') as cache_backend:
                with self.assertLogs('django', level='WARNING') as captured:
                    response = self.client.get(reverse('core:health_ready'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'unavailable'})
        self.assertNotIn(secret, ''.join(captured.output))
        cache_backend.set.assert_not_called()

    def test_cache_failure_returns_503_without_exception_details(self):
        with patch('core.health.cache.set', side_effect=ConnectionError('redis://private-secret')):
            response = self.client.get(reverse('core:health_ready'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'unavailable'})

    def test_silent_cache_failure_is_detected(self):
        with override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}):
            response = self.client.get(reverse('core:health_ready'))
        self.assertEqual(response.status_code, 503)

    def test_probe_keys_are_unique_cleaned_up_and_do_not_change_application_data(self):
        application_key = 'health-test:existing-app-data'
        cache.set(application_key, 'keep', timeout=60)
        try:
            with patch('core.health.cache.set', wraps=cache.set) as writes:
                for _ in range(2):
                    self.assertEqual(self.client.get(reverse('core:health_ready')).status_code, 200)
            keys = [call.args[0] for call in writes.call_args_list]
            self.assertEqual(len(set(keys)), 2)
            self.assertTrue(all(key.startswith('health:ready:') for key in keys))
            self.assertTrue(all(cache.get(key) is None for key in keys))
            self.assertEqual(cache.get(application_key), 'keep')
        finally:
            cache.delete(application_key)

    def test_read_failure_still_cleans_up_probe_key(self):
        with patch('core.health.cache.get', side_effect=TimeoutError('private detail')):
            with patch('core.health.cache.delete', wraps=cache.delete) as cleanup:
                response = self.client.get(reverse('core:health_ready'))
        self.assertEqual(response.status_code, 503)
        cleanup.assert_called_once()
        self.assertIsNone(cache.get(cleanup.call_args.args[0]))

    def test_cache_cleanup_failure_returns_unavailable(self):
        with patch('core.health.cache.delete', side_effect=TimeoutError):
            response = self.client.get(reverse('core:health_ready'))
        self.assertEqual(response.status_code, 503)

    def test_probes_support_head_reject_post_and_disallow_caching(self):
        for name in ('health_live', 'health_ready'):
            with self.subTest(name=name):
                url = reverse('core:' + name)
                response = self.client.head(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b'')
                self.assertIn('no-store', response['Cache-Control'])
                self.assertIn('no-cache', response['Cache-Control'])
                self.assertEqual(self.client.post(url).status_code, 405)

    def test_probes_preserve_https_redirects(self):
        with override_settings(SECURE_SSL_REDIRECT=True):
            for name in ('health_live', 'health_ready'):
                url = reverse('core:' + name)
                self.assertEqual(self.client.get(url).status_code, 301)
                self.assertEqual(self.client.get(url, secure=True).status_code, 200)
