from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

import manage
from config.monitoring import (
    configure_sentry,
    get_traces_sample_rate,
    scrub_sensitive_event_values,
)


class BaseTemplatePartialTests(TestCase):
    def test_anonymous_page_renders_shared_partials(self):
        response = self.client.get(reverse('core:home'))

        self.assertContains(response, 'aria-label="Primary navigation"')
        self.assertContains(response, 'bootstrap@5.3.3')
        self.assertContains(response, 'static/css/main.css')
        self.assertContains(response, 'static/js/main.js')
        self.assertContains(response, 'Log in')
        self.assertContains(response, reverse('core:help'))
        self.assertContains(response, 'class="app-nav__toggle"')
        self.assertContains(response, 'hidden')

    def test_authenticated_header_renders_from_partial(self):
        user = get_user_model().objects.create_user(
            email='member@example.com',
            password='password',
        )
        self.client.force_login(user)
        response = self.client.get(reverse('core:home'))

        self.assertContains(response, 'member@example.com')
        self.assertContains(response, 'action="/accounts/logout/"')


class PublicHelpPageTests(TestCase):
    def test_help_page_is_public_and_covers_both_first_use_paths(self):
        response = self.client.get(reverse('core:help'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Get started with Construction Manager')
        self.assertContains(response, 'Set up the company workspace')
        self.assertContains(response, 'Use the client portal')
        self.assertContains(response, 'Connect and synchronize QuickBooks')
        self.assertContains(response, 'Clients do not have access to the project schedule')
        self.assertContains(response, 'Install Construction Manager on a phone')
        self.assertContains(response, 'Add to Home Screen')

    def test_help_page_links_anonymous_users_to_login(self):
        response = self.client.get(reverse('core:help'))

        self.assertContains(response, reverse('login'))
        self.assertContains(response, '>Log in</a>', html=False)

    def test_help_page_links_authenticated_users_to_projects(self):
        user = get_user_model().objects.create_user(
            email='member@example.com',
            password='password',
        )
        self.client.force_login(user)

        response = self.client.get(reverse('core:help'))

        self.assertContains(response, reverse('core:home'))
        self.assertContains(response, 'Open your projects')


class ProgressiveWebAppTests(TestCase):
    def test_base_template_exposes_install_metadata(self):
        response = self.client.get(reverse('core:help'))

        self.assertContains(
            response,
            'rel="manifest" href="/static/manifest.webmanifest"',
        )
        self.assertContains(
            response,
            'rel="apple-touch-icon" href="/static/images/app-icon-180.png"',
        )
        self.assertContains(
            response,
            'rel="icon" href="/static/images/app-icon.svg"',
        )
        self.assertContains(response, 'name="theme-color"')
        self.assertContains(response, reverse('core:service_worker'))

    def test_service_worker_is_public_and_not_cacheable(self):
        response = self.client.get(reverse('core:service_worker'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/javascript')
        self.assertEqual(response['Service-Worker-Allowed'], '/')
        self.assertEqual(
            response['Cache-Control'],
            'no-cache, no-store, must-revalidate',
        )
        self.assertContains(response, 'self.skipWaiting()')
        self.assertContains(response, "addEventListener('fetch'")
        self.assertContains(response, reverse('core:offline'))
        self.assertContains(response, "event.request.method !== 'GET'")
        self.assertContains(response, "{cache: 'no-store'}")
        self.assertContains(response, '.catch(() => undefined)')
        self.assertNotContains(response, 'cache.put')

    def test_service_worker_skips_request_context_processors(self):
        user = get_user_model().objects.create_user(
            email='member@example.com',
            password='password',
        )
        self.client.force_login(user)

        with patch(
            'projects.context_processors.workspace_navigation',
            side_effect=AssertionError('context processor should not run'),
        ):
            response = self.client.get(reverse('core:service_worker'))

        self.assertEqual(response.status_code, 200)

    def test_offline_fallback_is_public_and_contains_no_project_data(self):
        response = self.client.get(reverse('core:offline'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'You are offline')
        self.assertContains(response, 'Reconnect and try again')

    def test_manifest_and_install_icons_are_collectable(self):
        expected_paths = (
            'manifest.webmanifest',
            'images/app-icon.svg',
            'images/app-icon-180.png',
            'images/app-icon-192.png',
            'images/app-icon-512.png',
            'images/app-icon-maskable-512.png',
        )

        for path in expected_paths:
            with self.subTest(path=path):
                self.assertIsNotNone(finders.find(path))


@override_settings(
    LEGAL_BUSINESS_NAME='Example Construction LLC',
    LEGAL_CONTACT_EMAIL='privacy@example.com',
    LEGAL_BUSINESS_ADDRESS='123 Main Street, Albany, NY 12207',
    LEGAL_GOVERNING_LAW='State of New York, United States',
    LEGAL_EFFECTIVE_DATE='July 31, 2026',
)
class PublicLegalPageTests(TestCase):
    def test_eula_is_public_and_contains_required_app_terms(self):
        response = self.client.get(reverse('core:eula'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'End-User License Agreement')
        self.assertContains(response, 'Example Construction LLC')
        self.assertContains(response, 'QuickBooks Online')
        self.assertContains(response, 'privacy@example.com')
        self.assertContains(response, 'State of New York, United States')

    def test_privacy_policy_is_public_and_covers_qbo_retention_and_deletion(self):
        response = self.client.get(reverse('core:privacy'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Privacy Policy')
        self.assertContains(response, 'OAuth')
        self.assertContains(response, 'Disconnecting QuickBooks')
        self.assertContains(response, 'Retention and deletion')
        self.assertContains(response, 'privacy@example.com')

    def test_public_footer_links_to_both_legal_pages(self):
        response = self.client.get(reverse('core:home'))

        self.assertContains(response, reverse('core:eula'))
        self.assertContains(response, reverse('core:privacy'))


class SentryConfigurationTests(SimpleTestCase):
    @patch('config.monitoring.sentry_sdk.init')
    def test_empty_dsn_leaves_monitoring_disabled(self, init_mock):
        enabled = configure_sentry(
            dsn='',
            environment='production',
            release='abc123',
            traces_sample_rate=0.05,
        )

        self.assertFalse(enabled)
        init_mock.assert_not_called()

    @patch('config.monitoring.sentry_sdk.init')
    def test_sentry_excludes_client_and_request_data(self, init_mock):
        enabled = configure_sentry(
            dsn='https://public@example.ingest.sentry.io/1',
            environment='production',
            release='abc123',
            traces_sample_rate=0.05,
        )

        self.assertTrue(enabled)
        options = init_mock.call_args.kwargs
        self.assertEqual(options['release'], 'abc123')
        self.assertEqual(options['sample_rate'], 1.0)
        self.assertEqual(options['traces_sample_rate'], 0.05)
        self.assertIs(options['before_send'], scrub_sensitive_event_values)
        self.assertIs(
            options['before_send_transaction'],
            scrub_sensitive_event_values,
        )
        self.assertFalse(options['data_collection']['user_info'])
        self.assertEqual(options['data_collection']['cookies']['mode'], 'off')
        self.assertEqual(
            options['data_collection']['http_headers']['request']['mode'],
            'off',
        )
        self.assertEqual(options['data_collection']['http_bodies'], [])
        self.assertEqual(
            options['data_collection']['url_query_params']['mode'],
            'off',
        )
        self.assertFalse(options['data_collection']['stack_frame_variables'])
        self.assertFalse(options['data_collection']['database_query_data'])

    @patch(
        'config.monitoring.sentry_sdk.init',
        side_effect=ValueError('malformed DSN'),
    )
    def test_malformed_dsn_disables_monitoring_without_blocking_startup(
        self,
        init_mock,
    ):
        with self.assertLogs('config.monitoring', level='WARNING') as logs:
            enabled = configure_sentry(
                dsn='not-a-dsn',
                environment='production',
                release='abc123',
                traces_sample_rate=0.05,
            )

        self.assertFalse(enabled)
        init_mock.assert_called_once()
        self.assertIn('SENTRY_DSN is invalid', logs.output[0])

    @patch('config.monitoring.sentry_sdk.init')
    def test_invalid_trace_sample_rate_fails_configuration(self, init_mock):
        with self.assertRaisesMessage(
            ValueError,
            'SENTRY_TRACES_SAMPLE_RATE must be between 0 and 1.',
        ):
            configure_sentry(
                dsn='https://public@example.ingest.sentry.io/1',
                environment='production',
                release='abc123',
                traces_sample_rate=1.1,
            )

        init_mock.assert_not_called()

    def test_disabled_sentry_ignores_invalid_trace_sample_rate(self):
        self.assertEqual(get_traces_sample_rate('', 'not-a-number'), 0.0)

    def test_enabled_sentry_rejects_invalid_trace_sample_rate(self):
        with self.assertRaises(ValueError):
            get_traces_sample_rate(
                'https://public@example.ingest.sentry.io/1',
                'not-a-number',
            )

    def test_event_scrubber_redacts_identifiers_in_nested_event_data(self):
        # Assemble the UUID so secret scanners do not mistake a public test
        # fixture for a committed credential.
        token = '-'.join(('00000000', '0000', '4000', '8000', '000000000000'))
        event = {
            'request': {
                'url': f'https://example.com/invitations/{token}/',
            },
            f'client@example.com:{token}': 'identifier-bearing key',
            'exception': {
                'values': [
                    {
                        'value': (
                            'Could not notify client@example.com at '
                            'https://example.com/callback/?code=secret-value'
                            '&access_token=access-value'
                            '&refresh_token=refresh-value'
                            '&client_secret=client-value'
                            '&api_key=key-value&ok=1'
                        )
                    }
                ]
            },
        }

        scrubbed = scrub_sensitive_event_values(event)

        self.assertEqual(
            scrubbed['request']['url'],
            'https://example.com/invitations/[Filtered UUID]/',
        )
        scrubbed_keys = ' '.join(str(key) for key in scrubbed)
        self.assertNotIn(token, scrubbed_keys)
        self.assertNotIn('client@example.com', scrubbed_keys)
        self.assertIn('[Filtered email]:[Filtered UUID]', scrubbed)
        exception_value = scrubbed['exception']['values'][0]['value']
        self.assertNotIn(token, exception_value)
        self.assertNotIn('client@example.com', exception_value)
        self.assertNotIn('secret-value', exception_value)
        self.assertNotIn('access-value', exception_value)
        self.assertNotIn('refresh-value', exception_value)
        self.assertNotIn('client-value', exception_value)
        self.assertNotIn('key-value', exception_value)
        self.assertIn('[Filtered email]', exception_value)
        self.assertIn('code=[Filtered]', exception_value)


class ManagementSettingsTests(SimpleTestCase):
    @patch.dict('os.environ', {'APP_ENVIRONMENT': 'production'}, clear=True)
    def test_production_management_commands_default_to_production_settings(self):
        self.assertEqual(manage.default_settings_module(), 'config.settings.prod')

    @patch.dict('os.environ', {}, clear=True)
    def test_local_management_commands_default_to_development_settings(self):
        self.assertEqual(manage.default_settings_module(), 'config.settings.dev')
