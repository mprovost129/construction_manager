from datetime import timedelta
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import requests
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.checks import run_checks
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from integrations.crypto import decrypt_token, encrypt_token
from integrations.models import QuickBooksConnection
from integrations.quickbooks import (
    QuickBooksOAuthClient,
    QuickBooksOAuthError,
    QuickBooksTokenResponse,
)
from integrations.services import refresh_connection
from projects.models import ActivityEvent, Organization, OrganizationMembership

TEST_FERNET_KEY = Fernet.generate_key().decode()
OLD_FERNET_KEY = Fernet.generate_key().decode()

QUICKBOOKS_SETTINGS = {
    'QUICKBOOKS_CONFIGURED': True,
    'QUICKBOOKS_ENVIRONMENT': 'sandbox',
    'QUICKBOOKS_CLIENT_ID': 'client-id',
    'QUICKBOOKS_CLIENT_SECRET': 'client-secret',
    'QUICKBOOKS_REDIRECT_URI': 'https://example.com/integrations/quickbooks/callback/',
    'QUICKBOOKS_TOKEN_ENCRYPTION_KEYS': (TEST_FERNET_KEY,),
}


def token_response(access='access-token', refresh='refresh-token'):
    return QuickBooksTokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=3600,
        refresh_token_expires_in=8640000,
        scopes=('com.intuit.quickbooks.accounting',),
    )


@override_settings(**QUICKBOOKS_SETTINGS)
class QuickBooksConnectionModelTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Acme', slug='acme')

    def test_tokens_are_encrypted_at_rest_and_not_rendered_by_str(self):
        connection = QuickBooksConnection(
            organization=self.organization,
            realm_id='12345',
            environment='sandbox',
        )
        connection.set_tokens(
            access_token='plain-access',
            refresh_token='plain-refresh',
        )
        connection.save()

        connection.refresh_from_db()
        self.assertNotEqual(connection.encrypted_access_token, 'plain-access')
        self.assertNotEqual(connection.encrypted_refresh_token, 'plain-refresh')
        self.assertEqual(connection.access_token, 'plain-access')
        self.assertEqual(connection.refresh_token, 'plain-refresh')
        self.assertNotIn('plain-access', str(connection))

    def test_old_key_can_decrypt_after_key_rotation(self):
        with override_settings(QUICKBOOKS_TOKEN_ENCRYPTION_KEYS=(OLD_FERNET_KEY,)):
            encrypted = encrypt_token('rotatable-secret')

        with override_settings(
            QUICKBOOKS_TOKEN_ENCRYPTION_KEYS=(TEST_FERNET_KEY, OLD_FERNET_KEY)
        ):
            self.assertEqual(decrypt_token(encrypted), 'rotatable-secret')


@override_settings(**QUICKBOOKS_SETTINGS)
class QuickBooksConnectionViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            email='admin@example.com', password='password'
        )
        self.staff = user_model.objects.create_user(
            email='staff@example.com', password='password'
        )
        self.organization = Organization.objects.create(name='Acme', slug='acme')
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.admin,
            role=OrganizationMembership.Role.ADMIN,
        )
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.staff,
            role=OrganizationMembership.Role.STAFF,
        )

    def _start_authorization(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('integrations:quickbooks_authorize'),
            {'organization': self.organization.slug},
        )
        self.assertEqual(response.status_code, 302)
        return parse_qs(urlparse(response.url).query)['state'][0]

    def test_connect_page_requires_login_and_only_lists_admin_companies(self):
        response = self.client.get(reverse('integrations:quickbooks_connect'))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.staff)
        response = self.client.get(reverse('integrations:quickbooks_connect'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Administrator access required')
        self.assertNotContains(response, 'Connect another company')

    def test_authorize_builds_intuit_url_and_saves_one_time_state(self):
        state = self._start_authorization()
        oauth_request = self.client.session['quickbooks_oauth_request']

        self.assertEqual(oauth_request['state'], state)
        self.assertEqual(oauth_request['organization_id'], self.organization.pk)
        response = self.client.post(
            reverse('integrations:quickbooks_authorize'),
            {'organization': self.organization.slug},
        )
        query = parse_qs(urlparse(response.url).query)
        self.assertEqual(query['client_id'], ['client-id'])
        self.assertEqual(query['response_type'], ['code'])
        self.assertEqual(query['scope'], ['com.intuit.quickbooks.accounting'])

    def test_non_admin_cannot_start_authorization(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('integrations:quickbooks_authorize'),
            {'organization': self.organization.slug},
        )
        self.assertEqual(response.status_code, 403)

    @patch('integrations.views.QuickBooksOAuthClient.exchange_code')
    def test_callback_rejects_invalid_state_without_exchanging_code(self, exchange):
        self._start_authorization()
        response = self.client.get(
            reverse('integrations:quickbooks_callback'),
            {'state': 'wrong-state', 'code': 'authorization-code', 'realmId': '123'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertNotIn('code=', response.url)
        exchange.assert_not_called()
        self.assertNotIn('quickbooks_oauth_request', self.client.session)

    @patch('integrations.views.QuickBooksOAuthClient.exchange_code')
    def test_denied_consent_does_not_exchange_or_change_connection(self, exchange):
        state = self._start_authorization()
        response = self.client.get(
            reverse('integrations:quickbooks_callback'),
            {'state': state, 'error': 'access_denied'},
        )

        self.assertEqual(response.status_code, 302)
        exchange.assert_not_called()
        self.assertFalse(QuickBooksConnection.objects.exists())

    @patch('integrations.views.QuickBooksOAuthClient.exchange_code')
    def test_expired_state_does_not_exchange_code(self, exchange):
        self._start_authorization()
        session = self.client.session
        oauth_request = session['quickbooks_oauth_request']
        oauth_request['created_at'] -= 601
        session['quickbooks_oauth_request'] = oauth_request
        session.save()
        response = self.client.get(
            reverse('integrations:quickbooks_callback'),
            {
                'state': oauth_request['state'],
                'code': 'authorization-code',
                'realmId': '12345',
            },
        )

        self.assertEqual(response.status_code, 302)
        exchange.assert_not_called()

    @patch('integrations.views.QuickBooksOAuthClient.exchange_code')
    def test_successful_callback_stores_encrypted_tokens_and_redirects_cleanly(
        self, exchange
    ):
        exchange.return_value = token_response()
        state = self._start_authorization()
        response = self.client.get(
            reverse('integrations:quickbooks_callback'),
            {'state': state, 'code': 'authorization-code', 'realmId': '12345'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertNotIn('authorization-code', response.url)
        connection = QuickBooksConnection.objects.get()
        self.assertEqual(connection.organization, self.organization)
        self.assertEqual(connection.realm_id, '12345')
        self.assertEqual(connection.access_token, 'access-token')
        self.assertNotIn('access-token', connection.encrypted_access_token)
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.QUICKBOOKS_CONNECTED
            ).exists()
        )

    @patch('integrations.views.QuickBooksOAuthClient.exchange_code')
    def test_same_realm_cannot_be_connected_to_another_organization(self, exchange):
        other = Organization.objects.create(name='Other', slug='other')
        QuickBooksConnection.objects.create(
            organization=other,
            realm_id='12345',
            environment='sandbox',
        )
        exchange.return_value = token_response()
        state = self._start_authorization()
        response = self.client.get(
            reverse('integrations:quickbooks_callback'),
            {'state': state, 'code': 'authorization-code', 'realmId': '12345'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            QuickBooksConnection.objects.get(realm_id='12345').organization,
            other,
        )

    @patch('integrations.quickbooks.requests.post')
    def test_disconnect_revokes_remote_token_and_clears_local_tokens(self, post):
        post.return_value = Mock(status_code=200)
        connection = QuickBooksConnection.objects.create(
            organization=self.organization,
            realm_id='12345',
            environment='sandbox',
        )
        connection.set_tokens(access_token='access', refresh_token='refresh')
        connection.save()
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('integrations:quickbooks_disconnect', args=(connection.pk,))
        )

        self.assertEqual(response.status_code, 302)
        connection.refresh_from_db()
        self.assertEqual(connection.status, QuickBooksConnection.Status.DISCONNECTED)
        self.assertEqual(connection.encrypted_refresh_token, '')
        self.assertEqual(post.call_args.kwargs['json'], {'token': 'refresh'})
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.QUICKBOOKS_DISCONNECTED
            ).exists()
        )

    def test_disconnect_requires_post(self):
        connection = QuickBooksConnection.objects.create(
            organization=self.organization,
            realm_id='12345',
            environment='sandbox',
        )
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('integrations:quickbooks_disconnect', args=(connection.pk,))
        )
        self.assertEqual(response.status_code, 405)

    def test_public_disconnected_landing_page_is_read_only(self):
        response = self.client.get(
            reverse('integrations:quickbooks_disconnected'),
            {'realmId': '12345'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Connection ended')
        self.assertContains(response, '12345')

    @patch('integrations.quickbooks.requests.post')
    def test_failed_remote_revoke_preserves_local_credentials(self, post):
        post.return_value = Mock(status_code=400)
        connection = QuickBooksConnection.objects.create(
            organization=self.organization,
            realm_id='12345',
            environment='sandbox',
        )
        connection.set_tokens(access_token='access', refresh_token='refresh')
        connection.save()
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('integrations:quickbooks_disconnect', args=(connection.pk,))
        )

        self.assertEqual(response.status_code, 302)
        connection.refresh_from_db()
        self.assertEqual(connection.status, QuickBooksConnection.Status.ERROR)
        self.assertEqual(connection.refresh_token, 'refresh')
        self.assertFalse(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.QUICKBOOKS_DISCONNECTED
            ).exists()
        )


@override_settings(**QUICKBOOKS_SETTINGS)
class QuickBooksOAuthClientTests(TestCase):
    @patch('integrations.quickbooks.requests.post')
    def test_exchange_parses_token_response_without_exposing_payload(self, post):
        post.return_value = Mock(
            status_code=200,
            json=Mock(
                return_value={
                    'access_token': 'access',
                    'refresh_token': 'refresh',
                    'expires_in': 3600,
                    'x_refresh_token_expires_in': 8640000,
                    'scope': 'com.intuit.quickbooks.accounting',
                }
            ),
        )
        result = QuickBooksOAuthClient().exchange_code('code')

        self.assertEqual(result.access_token, 'access')
        self.assertEqual(post.call_args.kwargs['timeout'], 15)
        self.assertEqual(post.call_args.kwargs['auth'], ('client-id', 'client-secret'))

    @patch('integrations.quickbooks.requests.post')
    def test_network_error_raises_safe_exception(self, post):
        post.side_effect = requests.Timeout('secret request details')

        with self.assertRaises(QuickBooksOAuthError) as raised:
            QuickBooksOAuthClient().exchange_code('sensitive-code')

        self.assertEqual(raised.exception.code, 'token_endpoint_unavailable')
        self.assertNotIn('sensitive-code', raised.exception.public_message)

    def test_refresh_serializes_and_saves_the_latest_refresh_token(self):
        organization = Organization.objects.create(name='Acme', slug='acme')
        connection = QuickBooksConnection.objects.create(
            organization=organization,
            realm_id='12345',
            environment='sandbox',
            access_token_expires_at=timezone.now() - timedelta(minutes=5),
        )
        connection.set_tokens(access_token='old-access', refresh_token='old-refresh')
        connection.save()
        oauth_client = Mock()
        oauth_client.refresh.return_value = token_response('new-access', 'new-refresh')

        refreshed = refresh_connection(connection.pk, client=oauth_client)

        oauth_client.refresh.assert_called_once_with('old-refresh')
        self.assertEqual(refreshed.access_token, 'new-access')
        self.assertEqual(refreshed.refresh_token, 'new-refresh')
        self.assertIsNotNone(refreshed.last_refreshed_at)

    def test_failed_refresh_requires_reauthorization_without_exposing_token(self):
        organization = Organization.objects.create(name='Acme', slug='acme')
        connection = QuickBooksConnection.objects.create(
            organization=organization,
            realm_id='12345',
            environment='sandbox',
        )
        connection.set_tokens(access_token='old-access', refresh_token='old-refresh')
        connection.save()
        oauth_client = Mock()
        oauth_client.refresh.side_effect = QuickBooksOAuthError(
            'token_exchange_failed', 'QuickBooks authorization expired.'
        )

        with self.assertRaises(QuickBooksOAuthError):
            refresh_connection(connection.pk, client=oauth_client)

        connection.refresh_from_db()
        self.assertEqual(
            connection.status,
            QuickBooksConnection.Status.REAUTHORIZATION_REQUIRED,
        )
        self.assertNotIn('old-refresh', connection.last_error_message)


class QuickBooksConfigurationCheckTests(TestCase):
    @override_settings(
        APP_ENVIRONMENT='production',
        QUICKBOOKS_CONFIGURED=True,
        QUICKBOOKS_ENVIRONMENT='sandbox',
    )
    def test_sandbox_configuration_is_rejected_under_production_settings(self):
        errors = run_checks()
        self.assertIn('integrations.E002', {error.id for error in errors})

    @override_settings(QUICKBOOKS_TOKEN_ENCRYPTION_KEYS=('not-a-fernet-key',))
    def test_invalid_encryption_key_is_rejected(self):
        errors = run_checks()
        self.assertIn('integrations.E003', {error.id for error in errors})
