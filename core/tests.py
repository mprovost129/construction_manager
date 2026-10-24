import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

import manage
from config import monitoring
from config.monitoring import (
    configure_sentry,
    get_traces_sample_rate,
    scrub_sensitive_event_values,
    sentry_configuration_is_active,
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
        with (
            patch('config.monitoring._configured_client', None),
        ):
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
        self.assertEqual(options['disabled_integrations'], [])
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

    @patch('config.monitoring.sentry_sdk.get_client')
    @patch('config.monitoring.sentry_sdk.init')
    def test_one_shot_disabled_integrations_keep_full_privacy_configuration(
        self,
        init_mock,
        get_client,
    ):
        integration = object()
        get_client.return_value = object()

        with patch('config.monitoring._configured_client', None):
            configure_sentry(
                dsn='https://public@example.ingest.sentry.io/1',
                environment='production',
                release='abc123',
                traces_sample_rate=0.05,
                disabled_integrations=(item for item in (integration,)),
            )

        options = init_mock.call_args.kwargs
        self.assertEqual(options['disabled_integrations'], [integration])
        self.assertIs(options['before_send'], scrub_sensitive_event_values)
        self.assertIs(
            options['before_send_transaction'],
            scrub_sensitive_event_values,
        )
        self.assertFalse(options['data_collection']['user_info'])
        self.assertEqual(options['data_collection']['http_bodies'], [])
        self.assertFalse(options['data_collection']['database_query_data'])

    @patch('config.monitoring.sentry_sdk.get_client')
    def test_active_configuration_requires_the_owned_client(self, get_client):
        owned_client = type('Client', (), {})()
        owned_client.options = {
            'before_send': scrub_sensitive_event_values,
            'before_send_transaction': scrub_sensitive_event_values,
            'data_collection': monitoring._privacy_data_collection(),
        }
        owned_client.get_integration = lambda integration: None
        replacement_client = object()

        configured_options = (
            'https://public@example.ingest.sentry.io/1',
            'production',
            'abc123',
            0.05,
            (),
        )
        with (
            patch('config.monitoring._configured_client', owned_client),
            patch('config.monitoring._configured_options', configured_options),
        ):
            get_client.return_value = owned_client
            self.assertTrue(sentry_configuration_is_active())

            get_client.return_value = replacement_client
            self.assertFalse(sentry_configuration_is_active())

    @patch('config.monitoring.sentry_sdk.get_client')
    def test_changed_privacy_options_make_configuration_inactive(self, get_client):
        owned_client = type('Client', (), {})()
        owned_client.options = {
            'before_send': None,
            'before_send_transaction': scrub_sensitive_event_values,
            'data_collection': monitoring._privacy_data_collection(),
        }
        owned_client.get_integration = lambda integration: None
        get_client.return_value = owned_client

        with (
            patch('config.monitoring._configured_client', owned_client),
            patch(
                'config.monitoring._configured_options',
                ('dsn', 'production', None, 0.05, ()),
            ),
        ):
            self.assertFalse(sentry_configuration_is_active())

    @patch('config.monitoring.sentry_sdk.get_client')
    @patch('config.monitoring.sentry_sdk.init')
    def test_later_public_initialization_is_blocked(self, init_mock, get_client):
        get_client.return_value = object()

        configure_sentry(
            dsn='https://public@example.ingest.sentry.io/1',
            environment='production',
            release='abc123',
            traces_sample_rate=0.05,
        )

        init_mock.assert_called_once()
        with self.assertRaisesMessage(RuntimeError, 'config.monitoring'):
            monitoring.sentry_sdk.init(dsn='https://other.invalid/1')

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

    def test_invalid_trace_sample_rate_disables_tracing_without_startup_failure(self):
        with self.assertLogs('config.monitoring', level='WARNING') as logs:
            sample_rate = get_traces_sample_rate(
                'https://public@example.ingest.sentry.io/1',
                'not-a-number',
            )

        self.assertEqual(sample_rate, 0.0)
        self.assertIn('performance tracing is disabled', logs.output[0])

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
                            '&api_key=key-value'
                            '&password=password-value'
                            '&auth=auth-value'
                            '&authorization=authorization-value'
                            '&access%54oken=encoded-token-value'
                            '&accessToken=camel-token-value'
                            '&clientSecret=camel-secret-value&ok=1.'
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
        self.assertNotIn('password-value', exception_value)
        self.assertNotIn('auth-value', exception_value)
        self.assertNotIn('authorization-value', exception_value)
        self.assertNotIn('encoded-token-value', exception_value)
        self.assertNotIn('camel-token-value', exception_value)
        self.assertNotIn('camel-secret-value', exception_value)
        self.assertIn('[Filtered email]', exception_value)
        self.assertIn('code=[Filtered]', exception_value)

    def test_database_exception_details_are_removed(self):
        event = {
            'message': 'duplicate key contains private client name',
            'logentry': {
                'message': 'Database insert failed: %s',
                'params': ['private client name'],
            },
            'extra': {'record_name': 'private client name'},
            'breadcrumbs': {
                'values': [
                    {
                        'category': 'query',
                        'message': 'private client name',
                    }
                ]
            },
            'exception': {
                'values': [
                    {
                        'module': 'builtins',
                        'type': 'RuntimeError',
                        'value': 'Could not save private client name',
                    },
                    {
                        'module': 'django.db.utils',
                        'type': 'IntegrityError',
                        'value': 'duplicate key contains private client name',
                        'stacktrace': {'frames': []},
                    }
                ]
            }
        }

        scrubbed = scrub_sensitive_event_values(event)

        wrapper_value, exception_value = scrubbed['exception']['values']
        self.assertEqual(
            wrapper_value['value'],
            '[Filtered database exception details]',
        )
        self.assertEqual(
            exception_value['value'],
            '[Filtered database exception details]',
        )
        self.assertEqual(exception_value['stacktrace'], {'frames': []})
        self.assertNotIn('message', scrubbed)
        self.assertNotIn('logentry', scrubbed)
        self.assertNotIn('extra', scrubbed)
        self.assertNotIn('breadcrumbs', scrubbed)

    def test_email_before_sentence_period_is_removed(self):
        scrubbed = scrub_sensitive_event_values(
            {'message': 'Notify client@example.com. Then retry.'}
        )

        self.assertEqual(
            scrubbed['message'],
            'Notify [Filtered email]. Then retry.',
        )


class ManagementSettingsTests(SimpleTestCase):
    @patch.dict('os.environ', {'APP_ENVIRONMENT': 'production'}, clear=True)
    def test_production_management_commands_default_to_production_settings(self):
        self.assertEqual(manage.default_settings_module(), 'config.settings.prod')

    @patch.dict('os.environ', {}, clear=True)
    def test_local_management_commands_default_to_development_settings(self):
        self.assertEqual(manage.default_settings_module(), 'config.settings.dev')

    @patch.dict('os.environ', {}, clear=True)
    @patch('manage.load_dotenv')
    def test_environment_file_is_loaded_before_selecting_settings(self, load_dotenv):
        load_dotenv.side_effect = lambda path: os.environ.__setitem__(
            'APP_ENVIRONMENT',
            'production',
        )

        self.assertEqual(manage.default_settings_module(), 'config.settings.prod')
        load_dotenv.assert_called_once_with(manage.BASE_DIR / '.env')

    @patch('manage.sentry_sdk.Scope')
    @patch('manage.ensure_management_sentry')
    def test_uncaught_management_command_error_is_reported(
        self,
        ensure_management_sentry,
        scope_class,
    ):
        client = ensure_management_sentry.return_value
        failure = RuntimeError('command failed')

        with self.assertRaises(RuntimeError):
            manage.execute_command(
                [],
                command_loader=lambda: (
                    lambda argv: (_ for _ in ()).throw(failure)
                ),
            )

        ensure_management_sentry.assert_called_once_with()
        scope_class.return_value.set_client.assert_called_once_with(client)
        scope_class.return_value.capture_exception.assert_called_once_with(failure)
        client.flush.assert_called_once_with(timeout=2)

    @patch('manage.sentry_sdk.Scope')
    @patch('manage.ensure_management_sentry')
    def test_failed_management_command_exit_is_reported(
        self,
        ensure_management_sentry,
        scope_class,
    ):
        client = ensure_management_sentry.return_value
        with self.assertRaises(SystemExit):
            manage.execute_command(
                [],
                command_loader=lambda: (
                    lambda argv: (_ for _ in ()).throw(SystemExit(1))
                ),
            )

        ensure_management_sentry.assert_called_once_with()
        scope_class.return_value.set_client.assert_called_once_with(client)
        scope_class.return_value.capture_message.assert_called_once_with(
            'Django management command failed with a non-zero exit status.',
            level='error',
        )
        client.flush.assert_called_once_with(timeout=2)

    @patch('manage.sentry_sdk.Scope')
    @patch('manage.ensure_management_sentry')
    def test_django_import_failure_is_reported(
        self,
        ensure_management_sentry,
        scope_class,
    ):
        client = ensure_management_sentry.return_value
        failure = ImportError('Django import failed')

        with self.assertRaises(ImportError):
            manage.execute_command(
                [],
                command_loader=lambda: (_ for _ in ()).throw(failure),
            )

        ensure_management_sentry.assert_called_once_with()
        scope_class.return_value.set_client.assert_called_once_with(client)
        scope_class.return_value.capture_exception.assert_called_once_with(failure)
        client.flush.assert_called_once_with(timeout=2)

    @patch('manage.sentry_sdk.Scope')
    @patch('manage.ensure_management_sentry', return_value=None)
    def test_unverified_client_does_not_receive_command_failure(
        self,
        ensure_management_sentry,
        scope_class,
    ):
        failure = RuntimeError('command failed')

        with self.assertRaises(RuntimeError) as raised:
            manage.execute_command(
                [],
                command_loader=lambda: (
                    lambda argv: (_ for _ in ()).throw(failure)
                ),
            )

        self.assertIs(raised.exception, failure)
        ensure_management_sentry.assert_called_once_with()
        scope_class.assert_not_called()

    @patch('manage.configure_sentry', side_effect=RuntimeError('setup failed'))
    @patch('manage.get_verified_sentry_client', return_value=None)
    def test_monitoring_setup_error_does_not_replace_command_failure(
        self,
        configuration_is_active,
        configure_sentry,
    ):
        failure = RuntimeError('command failed')

        with patch.dict(
            'os.environ',
            {'SENTRY_MANAGEMENT_COMMAND': '1', 'SENTRY_DSN': 'dsn'},
            clear=True,
        ):
            with self.assertRaises(RuntimeError) as raised:
                manage.execute_command(
                    [],
                    command_loader=lambda: (
                        lambda argv: (_ for _ in ()).throw(failure)
                    ),
                )

        self.assertIs(raised.exception, failure)

    @patch('manage.sentry_sdk.Scope')
    @patch('manage.ensure_management_sentry')
    def test_monitoring_capture_error_does_not_replace_command_failure(
        self,
        ensure_management_sentry,
        scope_class,
    ):
        scope_class.return_value.capture_exception.side_effect = RuntimeError(
            'sdk failed'
        )
        failure = RuntimeError('command failed')

        with self.assertRaises(RuntimeError) as raised:
            manage.execute_command(
                [],
                command_loader=lambda: (
                    lambda argv: (_ for _ in ()).throw(failure)
                ),
            )

        self.assertIs(raised.exception, failure)

    def test_management_command_is_marked_before_django_settings_load(self):
        with patch.dict(
            'os.environ',
            {
                'SENTRY_DSN': 'https://public@example.ingest.sentry.io/1',
                'SENTRY_TRACES_SAMPLE_RATE': '0.05',
                'RENDER_GIT_COMMIT': 'abc123',
            },
            clear=True,
        ):
            manage.configure_management_sentry('config.settings.prod')
            self.assertEqual(os.environ['SENTRY_MANAGEMENT_COMMAND'], '1')

    @patch('manage.configure_sentry')
    @patch('manage.get_verified_sentry_client', return_value=None)
    def test_startup_failure_installs_monitoring_when_settings_did_not(
        self,
        configuration_is_active,
        configure_sentry,
    ):
        verified_client = object()
        configuration_is_active.side_effect = (None, verified_client)
        configure_sentry.return_value = True
        with patch.dict(
            'os.environ',
            {
                'SENTRY_MANAGEMENT_COMMAND': '1',
                'SENTRY_DSN': 'https://public@example.ingest.sentry.io/1',
                'SENTRY_TRACES_SAMPLE_RATE': '0.05',
                'RENDER_GIT_COMMIT': 'abc123',
            },
            clear=True,
        ):
            manage.ensure_management_sentry()

        configure_sentry.assert_called_once_with(
            dsn='https://public@example.ingest.sentry.io/1',
            environment='production',
            release='abc123',
            traces_sample_rate=0.05,
            disabled_integrations=(manage.ExcepthookIntegration,),
        )
        self.assertEqual(configuration_is_active.call_count, 2)

    @patch('manage.configure_sentry')
    @patch('manage.get_verified_sentry_client')
    def test_active_settings_client_is_preserved(
        self,
        configuration_is_active,
        configure_sentry,
    ):
        verified_client = object()
        configuration_is_active.return_value = verified_client
        with patch.dict(
            'os.environ',
            {'SENTRY_MANAGEMENT_COMMAND': '1'},
            clear=True,
        ):
            self.assertIs(manage.ensure_management_sentry(), verified_client)

        configuration_is_active.assert_called_once_with(
            dsn='',
            environment='production',
            release='',
            traces_sample_rate=0.0,
            disabled_integrations=(manage.ExcepthookIntegration,),
        )
        configure_sentry.assert_not_called()

    @patch('manage.configure_sentry', return_value=True)
    @patch(
        'manage.get_verified_sentry_client',
    )
    def test_inactive_settings_client_is_reconfigured(
        self,
        configuration_is_active,
        configure_sentry,
    ):
        verified_client = object()
        configuration_is_active.side_effect = (None, verified_client)
        with patch.dict(
            'os.environ',
            {'SENTRY_MANAGEMENT_COMMAND': '1', 'SENTRY_DSN': 'dsn'},
            clear=True,
        ):
            self.assertIs(manage.ensure_management_sentry(), verified_client)

        configure_sentry.assert_called_once()
        self.assertEqual(configuration_is_active.call_count, 2)
