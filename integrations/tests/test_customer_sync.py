from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from integrations.customer_sync import (
    QuickBooksSyncBusy,
    QuickBooksSyncError,
    execute_customer_sync_attempt,
    resolve_customer_sync_attempt,
    retry_customer_sync_attempt,
    start_project_customer_sync,
)
from integrations.models import (
    QuickBooksConnection,
    QuickBooksProjectCustomerMapping,
    QuickBooksSyncAttempt,
)
from integrations.quickbooks import QuickBooksAccountingClient, QuickBooksAPIError
from integrations.services import save_project_customer_mapping
from projects.models import Organization, OrganizationMembership, Project

TEST_KEY = Fernet.generate_key().decode()
SYNC_SETTINGS = {
    'QUICKBOOKS_CONFIGURED': True,
    'QUICKBOOKS_ENVIRONMENT': 'sandbox',
    'QUICKBOOKS_CLIENT_ID': 'client-id',
    'QUICKBOOKS_CLIENT_SECRET': 'client-secret',
    'QUICKBOOKS_REDIRECT_URI': 'https://example.com/callback/',
    'QUICKBOOKS_TOKEN_ENCRYPTION_KEYS': (TEST_KEY,),
    'QUICKBOOKS_MINOR_VERSION': 75,
    'QUICKBOOKS_SYNC_MAX_ATTEMPTS': 5,
    'QUICKBOOKS_SYNC_RETRY_BASE_SECONDS': 60,
}


def customer(customer_id='42', name='Smith Residence', sync_token='0'):
    return {
        'Id': customer_id,
        'SyncToken': sync_token,
        'DisplayName': name,
        'Active': True,
        'IsProject': False,
    }


@override_settings(**SYNC_SETTINGS)
class QuickBooksCustomerWriteClientTests(TestCase):
    def setUp(self):
        organization = Organization.objects.create(name='Acme', slug='acme')
        self.connection = QuickBooksConnection.objects.create(
            organization=organization,
            realm_id='12345',
            environment='sandbox',
        )
        self.connection.set_tokens(access_token='access', refresh_token='refresh')
        self.connection.save()

    @patch('integrations.quickbooks.requests.post')
    def test_create_uses_stable_request_id(self, post):
        post.return_value = Mock(
            status_code=200,
            json=Mock(return_value={'Customer': customer()}),
        )

        result = QuickBooksAccountingClient().create_customer(
            self.connection,
            {'DisplayName': 'Smith Residence'},
            request_id='stable-request-id',
        )

        self.assertEqual(result['Id'], '42')
        self.assertEqual(post.call_args.kwargs['params']['requestid'], 'stable-request-id')
        self.assertEqual(
            post.call_args.kwargs['json'],
            {'DisplayName': 'Smith Residence'},
        )

    @patch('integrations.quickbooks.requests.post')
    def test_sparse_update_requires_and_sends_sync_token(self, post):
        post.return_value = Mock(
            status_code=200,
            json=Mock(return_value={'Customer': customer(sync_token='4')}),
        )

        QuickBooksAccountingClient().update_customer(
            self.connection,
            {'Id': '42', 'SyncToken': '3', 'DisplayName': 'Updated'},
            request_id='update-request',
        )

        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['SyncToken'], '3')
        self.assertTrue(payload['sparse'])

    @patch('integrations.quickbooks.requests.get')
    def test_customer_query_paginates_until_short_page(self, get):
        get.side_effect = [
            Mock(
                status_code=200,
                json=Mock(
                    return_value={
                        'QueryResponse': {
                            'Customer': [customer('1'), customer('2')],
                        }
                    }
                ),
            ),
            Mock(
                status_code=200,
                json=Mock(
                    return_value={'QueryResponse': {'Customer': [customer('3')]}}
                ),
            ),
        ]

        customers = list(
            QuickBooksAccountingClient().iter_customers(
                self.connection,
                page_size=2,
            )
        )

        self.assertEqual([item['Id'] for item in customers], ['1', '2', '3'])
        first_query = get.call_args_list[0].kwargs['params']['query']
        second_query = get.call_args_list[1].kwargs['params']['query']
        self.assertIn('STARTPOSITION 1 MAXRESULTS 2', first_query)
        self.assertIn('STARTPOSITION 3 MAXRESULTS 2', second_query)

    @patch('integrations.quickbooks.requests.post')
    def test_rate_limit_error_is_retryable_and_sanitized(self, post):
        post.return_value = Mock(status_code=429, json=Mock(return_value={}))

        with self.assertRaises(QuickBooksAPIError) as raised:
            QuickBooksAccountingClient().create_customer(
                self.connection,
                {'DisplayName': 'Smith Residence'},
                request_id='request-id',
            )

        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.status_code, 429)


@override_settings(**SYNC_SETTINGS)
class QuickBooksCustomerSyncTests(TestCase):
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
            capabilities={'accounting_write': True},
            capabilities_checked_at=timezone.now(),
        )
        self.connection.set_tokens(access_token='access', refresh_token='refresh')
        self.connection.save()

    def test_unmapped_project_creates_customer_and_durable_success(self):
        api = Mock()
        api.find_customers_by_display_name.return_value = []
        api.create_customer.return_value = customer()

        attempt = start_project_customer_sync(
            project_id=self.project.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        self.assertEqual(attempt.external_id, '42')
        self.assertTrue(
            QuickBooksProjectCustomerMapping.objects.filter(
                project=self.project,
                quickbooks_customer_id='42',
            ).exists()
        )
        request_id = api.create_customer.call_args.kwargs['request_id']
        self.assertEqual(request_id, attempt.request_id)

    def test_existing_name_is_mapped_without_duplicate_create(self):
        api = Mock()
        api.find_customers_by_display_name.return_value = [customer()]

        attempt = start_project_customer_sync(
            project_id=self.project.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        api.create_customer.assert_not_called()

    def test_mapped_customer_refreshes_from_quickbooks(self):
        mapping = save_project_customer_mapping(
            project=self.project,
            connection=self.connection,
            customer=customer(),
            actor=self.admin,
        )
        api = Mock()
        api.get_customer.return_value = customer(name='QuickBooks Name', sync_token='4')

        attempt = start_project_customer_sync(
            project_id=self.project.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

        mapping.refresh_from_db()
        self.assertEqual(attempt.operation, QuickBooksSyncAttempt.Operation.READ)
        self.assertEqual(mapping.quickbooks_display_name, 'QuickBooks Name')
        self.assertEqual(mapping.quickbooks_sync_token, '4')

    def test_mapped_customer_rejects_a_different_company_connection(self):
        save_project_customer_mapping(
            project=self.project,
            connection=self.connection,
            customer=customer(),
            actor=self.admin,
        )
        other_connection = QuickBooksConnection.objects.create(
            organization=self.organization,
            realm_id='67890',
            environment='sandbox',
            capabilities={'accounting_write': True},
            capabilities_checked_at=timezone.now(),
        )

        with self.assertRaisesMessage(
            QuickBooksSyncError,
            'mapped QuickBooks company',
        ):
            start_project_customer_sync(
                project_id=self.project.pk,
                connection_id=other_connection.pk,
                actor=self.admin,
                api_client=Mock(),
            )

    def test_missing_customer_tombstones_mapping_as_success(self):
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

        attempt = start_project_customer_sync(
            project_id=self.project.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

        mapping.refresh_from_db()
        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        self.assertEqual(mapping.status, QuickBooksProjectCustomerMapping.Status.TOMBSTONED)
        self.assertEqual(mapping.last_synced_values['Id'], '42')

    def test_retryable_failure_records_backoff_without_secret_details(self):
        api = Mock()
        api.find_customers_by_display_name.return_value = []
        api.create_customer.side_effect = QuickBooksAPIError(
            'api_unavailable',
            'QuickBooks could not be reached. Try again later.',
            retryable=True,
        )

        attempt = start_project_customer_sync(
            project_id=self.project.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.FAILED)
        self.assertTrue(attempt.retryable)
        self.assertGreaterEqual(
            attempt.next_retry_at,
            attempt.completed_at + timedelta(seconds=59),
        )
        self.assertNotIn('access', attempt.error_message)

    def test_retry_reuses_request_id_and_resolves_previous_attempt(self):
        first_api = Mock()
        first_api.find_customers_by_display_name.return_value = []
        first_api.create_customer.side_effect = QuickBooksAPIError(
            'api_unavailable',
            'QuickBooks could not be reached.',
            retryable=True,
        )
        failed = start_project_customer_sync(
            project_id=self.project.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=first_api,
        )
        original_request_id = first_api.create_customer.call_args.kwargs['request_id']
        retry_api = Mock()
        retry_api.find_customers_by_display_name.return_value = []
        retry_api.create_customer.return_value = customer()

        succeeded = retry_customer_sync_attempt(
            failed.pk,
            actor=self.admin,
            api_client=retry_api,
        )

        failed.refresh_from_db()
        self.assertEqual(succeeded.attempt_number, 2)
        self.assertEqual(
            retry_api.create_customer.call_args.kwargs['request_id'],
            original_request_id,
        )
        self.assertEqual(failed.status, QuickBooksSyncAttempt.Status.RESOLVED)

    def test_read_only_company_records_nonretryable_failure(self):
        self.connection.capabilities = {'accounting_write': False}
        self.connection.save()
        api = Mock()

        attempt = start_project_customer_sync(
            project_id=self.project.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.FAILED)
        self.assertEqual(attempt.error_code, '6190')
        self.assertFalse(attempt.retryable)
        api.find_customers_by_display_name.assert_not_called()

    def test_customer_create_requires_current_capability_check(self):
        self.connection.capabilities_checked_at = None
        self.connection.save()

        attempt = start_project_customer_sync(
            project_id=self.project.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=Mock(),
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.FAILED)
        self.assertEqual(attempt.error_code, 'capabilities_unknown')

    def test_update_fetches_latest_sync_token_before_sparse_write(self):
        attempt = QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            project=self.project,
            entity_type=QuickBooksSyncAttempt.EntityType.CUSTOMER,
            operation=QuickBooksSyncAttempt.Operation.UPDATE,
            direction=QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.RUNNING,
            request_payload={'Id': '42', 'DisplayName': 'Updated Name'},
        )
        api = Mock()
        api.get_customer.return_value = customer(sync_token='7')
        api.update_customer.return_value = customer(
            name='Updated Name',
            sync_token='8',
        )

        result = execute_customer_sync_attempt(attempt.pk, api_client=api)

        self.assertEqual(result.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        update_payload = api.update_customer.call_args.args[1]
        self.assertEqual(update_payload['SyncToken'], '7')

    def test_running_project_sync_prevents_concurrent_duplicate(self):
        other_project = Project.objects.create(
            organization=self.organization,
            name='Jones Residence',
        )
        QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            project=other_project,
            entity_type=QuickBooksSyncAttempt.EntityType.CUSTOMER,
            operation=QuickBooksSyncAttempt.Operation.CREATE,
            direction=QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.RUNNING,
        )

        with self.assertRaises(QuickBooksSyncBusy):
            start_project_customer_sync(
                project_id=self.project.pk,
                connection_id=self.connection.pk,
                actor=self.admin,
                api_client=Mock(),
            )

    def test_failed_attempt_can_be_resolved_with_note(self):
        attempt = QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            project=self.project,
            entity_type=QuickBooksSyncAttempt.EntityType.CUSTOMER,
            operation=QuickBooksSyncAttempt.Operation.CREATE,
            direction=QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.FAILED,
            error_code='6240',
            error_message='Duplicate name.',
        )

        attempt = resolve_customer_sync_attempt(
            attempt.pk,
            actor=self.admin,
            note='Mapped the existing customer instead.',
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.RESOLVED)
        self.assertEqual(attempt.resolved_by, self.admin)
        self.assertIn('Mapped', attempt.resolution_note)

    @patch('integrations.views.start_project_customer_sync')
    def test_admin_can_start_sync_from_ui(self, start_sync):
        start_sync.return_value = QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            project=self.project,
            entity_type=QuickBooksSyncAttempt.EntityType.CUSTOMER,
            operation=QuickBooksSyncAttempt.Operation.CREATE,
            direction=QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.SUCCEEDED,
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('integrations:quickbooks_customer_sync', args=(self.project.pk,)),
            {'connection': self.connection.pk},
        )

        self.assertEqual(response.status_code, 302)
        start_sync.assert_called_once_with(
            project_id=self.project.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
        )

    def test_non_admin_cannot_start_sync(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('integrations:quickbooks_customer_sync', args=(self.project.pk,)),
            {'connection': self.connection.pk},
        )
        self.assertEqual(response.status_code, 403)

    def test_failed_attempt_appears_in_admin_error_queue(self):
        QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            project=self.project,
            entity_type=QuickBooksSyncAttempt.EntityType.CUSTOMER,
            operation=QuickBooksSyncAttempt.Operation.CREATE,
            direction=QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.FAILED,
            error_code='6240',
            error_message='That name is already in use.',
        )
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse('integrations:quickbooks_connect'),
            {'organization': self.organization.slug},
        )

        self.assertContains(response, 'Customer sync error queue')
        self.assertContains(response, 'That name is already in use.')
        self.assertContains(response, 'Mark resolved')

    @patch(
        'integrations.management.commands.retry_quickbooks_syncs.retry_customer_sync_attempt'
    )
    def test_retry_command_processes_due_attempts(self, retry):
        attempt = QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            project=self.project,
            entity_type=QuickBooksSyncAttempt.EntityType.CUSTOMER,
            operation=QuickBooksSyncAttempt.Operation.CREATE,
            direction=QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.FAILED,
            retryable=True,
            next_retry_at=timezone.now() - timedelta(minutes=1),
        )
        retry.return_value = Mock(status=QuickBooksSyncAttempt.Status.SUCCEEDED)
        output = StringIO()

        call_command('retry_quickbooks_syncs', stdout=output)

        retry.assert_called_once_with(attempt.pk, actor=None)
        self.assertIn('1 succeeded', output.getvalue())
