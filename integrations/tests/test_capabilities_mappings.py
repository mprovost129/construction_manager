from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from integrations.models import (
    QuickBooksConnection,
    QuickBooksProjectCustomerMapping,
)
from integrations.quickbooks import QuickBooksAccountingClient, QuickBooksAPIError
from integrations.services import (
    QuickBooksMappingError,
    build_capability_profile,
    record_capability_unavailable,
    refresh_company_capabilities,
    refresh_project_customer_mapping,
    save_project_customer_mapping,
    unlink_project_customer_mapping,
)
from integrations.sync_policy import ENTITY_SYNC_POLICIES
from projects.models import (
    ActivityEvent,
    Organization,
    OrganizationMembership,
    Project,
)

TEST_KEY = Fernet.generate_key().decode()
QBO_SETTINGS = {
    'QUICKBOOKS_CONFIGURED': True,
    'QUICKBOOKS_ENVIRONMENT': 'sandbox',
    'QUICKBOOKS_CLIENT_ID': 'client-id',
    'QUICKBOOKS_CLIENT_SECRET': 'client-secret',
    'QUICKBOOKS_REDIRECT_URI': 'https://example.com/callback/',
    'QUICKBOOKS_TOKEN_ENCRYPTION_KEYS': (TEST_KEY,),
    'QUICKBOOKS_MINOR_VERSION': 75,
}


def company_info(status='SUBSCRIBED'):
    return {
        'CompanyName': 'Acme QuickBooks',
        'LegalName': 'Acme Builders LLC',
        'Country': 'US',
        'SubscriptionStatus': status,
        'NameValue': [
            {'Name': 'OfferingSku', 'Value': 'QuickBooks Online Plus'},
        ],
    }


def preferences():
    return {
        'SalesFormsPrefs': {
            'CustomTxnNumbers': True,
            'UsingProgressInvoicing': False,
        },
        'CurrencyPrefs': {'MultiCurrencyEnabled': False},
        'AccountingInfoPrefs': {
            'ClassTrackingPerTxn': True,
            'TrackDepartments': False,
        },
    }


def customer(customer_id='42', *, is_project=False):
    return {
        'Id': customer_id,
        'SyncToken': '3',
        'DisplayName': 'Smith Residence',
        'Active': True,
        'IsProject': is_project,
    }


@override_settings(**QBO_SETTINGS)
class QuickBooksAccountingClientTests(TestCase):
    def setUp(self):
        organization = Organization.objects.create(name='Acme', slug='acme')
        self.connection = QuickBooksConnection.objects.create(
            organization=organization,
            realm_id='12345',
            environment='sandbox',
        )
        self.connection.set_tokens(access_token='access', refresh_token='refresh')
        self.connection.save()

    @patch('integrations.quickbooks.requests.get')
    def test_company_info_uses_sandbox_host_bearer_token_and_minor_version(self, get):
        get.return_value = Mock(
            status_code=200,
            json=Mock(return_value={'CompanyInfo': company_info()}),
        )

        result = QuickBooksAccountingClient().get_company_info(self.connection)

        self.assertEqual(result['CompanyName'], 'Acme QuickBooks')
        call = get.call_args
        self.assertIn('sandbox-quickbooks.api.intuit.com', call.args[0])
        self.assertEqual(call.kwargs['params'], {'minorversion': 75})
        self.assertEqual(call.kwargs['headers']['Authorization'], 'Bearer access')

    @patch('integrations.quickbooks.requests.get')
    def test_api_fault_is_sanitized_and_identifies_unsupported_feature(self, get):
        get.return_value = Mock(
            status_code=400,
            json=Mock(
                return_value={
                    'Fault': {
                        'Error': [
                            {
                                'code': '5030',
                                'Detail': 'sensitive upstream detail',
                            }
                        ]
                    }
                }
            ),
        )

        with self.assertRaises(QuickBooksAPIError) as raised:
            QuickBooksAccountingClient().get_preferences(self.connection)

        self.assertTrue(raised.exception.is_feature_unsupported)
        self.assertNotIn('sensitive', raised.exception.public_message)

    @patch('integrations.quickbooks.requests.get')
    @patch('integrations.services.refresh_connection')
    def test_unauthorized_response_refreshes_once_and_retries(self, refresh, get):
        def refreshed(connection_id):
            self.connection.set_tokens(
                access_token='new-access',
                refresh_token='new-refresh',
            )
            self.connection.save()
            return self.connection

        refresh.side_effect = refreshed
        get.side_effect = [
            Mock(status_code=401, json=Mock(return_value={})),
            Mock(
                status_code=200,
                json=Mock(return_value={'CompanyInfo': company_info()}),
            ),
        ]

        QuickBooksAccountingClient().get_company_info(self.connection)

        refresh.assert_called_once_with(self.connection.pk)
        self.assertEqual(
            get.call_args_list[1].kwargs['headers']['Authorization'],
            'Bearer new-access',
        )


@override_settings(**QBO_SETTINGS)
class QuickBooksCapabilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email='admin@example.com')
        self.organization = Organization.objects.create(name='Acme', slug='acme')
        self.connection = QuickBooksConnection.objects.create(
            organization=self.organization,
            realm_id='12345',
            environment='sandbox',
        )

    def test_subscription_state_controls_write_without_edition_assumption(self):
        active = build_capability_profile(company_info('SUBSCRIBED'), preferences())
        restricted = build_capability_profile(company_info('RESTRICTED'), preferences())

        self.assertTrue(active['accounting_write'])
        self.assertTrue(restricted['accounting_read'])
        self.assertFalse(restricted['accounting_write'])
        self.assertTrue(active['class_tracking'])
        self.assertFalse(active['progress_invoicing'])

    def test_refresh_stores_company_metadata_and_capabilities(self):
        api = Mock()
        api.get_company_info.return_value = company_info()
        api.get_preferences.return_value = preferences()

        connection = refresh_company_capabilities(
            self.connection.pk,
            actor=self.user,
            api_client=api,
        )

        self.assertEqual(connection.company_name, 'Acme QuickBooks')
        self.assertEqual(connection.subscription_status, 'SUBSCRIBED')
        self.assertTrue(connection.capabilities['accounting_write'])
        self.assertIsNotNone(connection.capabilities_checked_at)
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.QUICKBOOKS_CAPABILITIES_REFRESHED
            ).exists()
        )

    def test_observed_feature_loss_preserves_other_capabilities(self):
        self.connection.capabilities = {
            'accounting_read': True,
            'invoice_write': True,
        }
        self.connection.save()
        error = QuickBooksAPIError('5030', 'Feature unavailable.')

        connection = record_capability_unavailable(
            self.connection.pk,
            'invoice_write',
            error,
        )

        self.assertTrue(connection.capabilities['accounting_read'])
        self.assertFalse(connection.capabilities['invoice_write'])
        self.assertIn(
            'invoice_write',
            connection.capabilities['observed_unsupported'],
        )

    def test_sync_policies_preserve_quickbooks_as_accounting_authority(self):
        self.assertEqual(
            ENTITY_SYNC_POLICIES['customer']['conflict_resolution'],
            'quickbooks_wins',
        )
        self.assertEqual(
            ENTITY_SYNC_POLICIES['invoice']['local_origin'],
            'drafts_allowed',
        )
        self.assertEqual(
            ENTITY_SYNC_POLICIES['payment']['source_of_truth'],
            'quickbooks',
        )


@override_settings(**QBO_SETTINGS)
class QuickBooksProjectMappingTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(email='admin@example.com')
        self.staff = user_model.objects.create_user(email='staff@example.com')
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
        self.project = Project.objects.create(
            organization=self.organization,
            name='Smith Residence',
        )
        self.connection = QuickBooksConnection.objects.create(
            organization=self.organization,
            realm_id='12345',
            environment='sandbox',
            company_name='Acme QuickBooks',
        )
        self.connection.set_tokens(access_token='access', refresh_token='refresh')
        self.connection.save()

    def test_mapping_saves_external_identity_sync_token_snapshot_and_audit(self):
        mapping = save_project_customer_mapping(
            project=self.project,
            connection=self.connection,
            customer=customer(),
            actor=self.admin,
        )

        self.assertEqual(mapping.quickbooks_customer_id, '42')
        self.assertEqual(mapping.quickbooks_sync_token, '3')
        self.assertEqual(mapping.last_synced_values['DisplayName'], 'Smith Residence')
        self.assertEqual(mapping.conflict_policy, 'quickbooks_wins')
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.QUICKBOOKS_PROJECT_MAPPED,
                project=self.project,
            ).exists()
        )

    def test_quickbooks_project_or_job_cannot_be_used_as_customer_mapping(self):
        with self.assertRaisesMessage(
            QuickBooksMappingError,
            'not a QuickBooks Project or Job',
        ):
            save_project_customer_mapping(
                project=self.project,
                connection=self.connection,
                customer=customer(is_project=True),
                actor=self.admin,
            )

    def test_customer_cannot_be_active_for_two_projects(self):
        save_project_customer_mapping(
            project=self.project,
            connection=self.connection,
            customer=customer(),
            actor=self.admin,
        )
        other_project = Project.objects.create(
            organization=self.organization,
            name='Jones Residence',
        )

        with self.assertRaisesMessage(QuickBooksMappingError, 'already mapped'):
            save_project_customer_mapping(
                project=other_project,
                connection=self.connection,
                customer=customer(),
                actor=self.admin,
            )

    def test_missing_external_customer_tombstones_without_deleting_snapshot(self):
        mapping = save_project_customer_mapping(
            project=self.project,
            connection=self.connection,
            customer=customer(),
            actor=self.admin,
        )
        api = Mock()
        api.get_customer.side_effect = QuickBooksAPIError(
            '610',
            'The requested QuickBooks record no longer exists.',
            status_code=400,
        )

        mapping = refresh_project_customer_mapping(
            mapping.pk,
            actor=self.admin,
            api_client=api,
        )

        self.assertEqual(
            mapping.status,
            QuickBooksProjectCustomerMapping.Status.TOMBSTONED,
        )
        self.assertEqual(mapping.last_synced_values['Id'], '42')
        self.assertIsNotNone(mapping.tombstoned_at)

    def test_unlink_preserves_mapping_history(self):
        mapping = save_project_customer_mapping(
            project=self.project,
            connection=self.connection,
            customer=customer(),
            actor=self.admin,
        )

        mapping = unlink_project_customer_mapping(mapping.pk, actor=self.admin)

        self.assertEqual(mapping.status, QuickBooksProjectCustomerMapping.Status.UNLINKED)
        self.assertEqual(mapping.quickbooks_customer_id, '42')
        self.assertIsNotNone(mapping.unlinked_at)

    @patch('integrations.views.QuickBooksAccountingClient.get_customer')
    def test_admin_can_validate_and_save_mapping_from_ui(self, get_customer):
        get_customer.return_value = customer()
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('integrations:quickbooks_mapping_save'),
            {
                'organization': self.organization.slug,
                'project': self.project.pk,
                'connection': self.connection.pk,
                'quickbooks_customer_id': '42',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            QuickBooksProjectCustomerMapping.objects.filter(
                project=self.project,
                quickbooks_customer_id='42',
            ).exists()
        )

    def test_admin_connection_page_exposes_capability_and_mapping_workflows(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('integrations:quickbooks_connect'),
            {'organization': self.organization.slug},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Refresh company info')
        self.assertContains(response, 'Project-to-customer mappings')
        self.assertContains(response, 'Validate and save mapping')

    @patch('integrations.views.refresh_company_capabilities')
    def test_admin_can_refresh_company_capabilities_from_ui(self, refresh):
        refresh.return_value = self.connection
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse(
                'integrations:quickbooks_capabilities_refresh',
                args=(self.connection.pk,),
            )
        )

        self.assertEqual(response.status_code, 302)
        refresh.assert_called_once_with(self.connection.pk, actor=self.admin)

    def test_non_admin_cannot_save_mapping(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('integrations:quickbooks_mapping_save'),
            {
                'organization': self.organization.slug,
                'project': self.project.pk,
                'connection': self.connection.pk,
                'quickbooks_customer_id': '42',
            },
        )
        self.assertEqual(response.status_code, 403)
