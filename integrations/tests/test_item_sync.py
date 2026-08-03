from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from integrations.item_sync import (
    QuickBooksSyncBusy,
    QuickBooksSyncError,
    execute_item_sync_attempt,
    resolve_item_sync_attempt,
    retry_item_sync_attempt,
    start_cost_code_item_sync,
)
from integrations.models import (
    QuickBooksConnection,
    QuickBooksItemMapping,
    QuickBooksSyncAttempt,
)
from integrations.quickbooks import QuickBooksAccountingClient, QuickBooksAPIError
from integrations.services import (
    QuickBooksMappingError,
    save_cost_code_item_mapping,
    unlink_cost_code_item_mapping,
)
from projects.models import CostCode, Organization, OrganizationMembership

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


def item(item_id='7', name='Framing labor', sync_token='0'):
    return {
        'Id': item_id,
        'SyncToken': sync_token,
        'Name': name,
        'Active': True,
        'Type': 'Service',
    }


@override_settings(**SYNC_SETTINGS)
class QuickBooksItemWriteClientTests(TestCase):
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
    def test_create_item_uses_stable_request_id(self, post):
        post.return_value = Mock(
            status_code=200,
            json=Mock(return_value={'Item': item()}),
        )

        result = QuickBooksAccountingClient().create_item(
            self.connection,
            {'Name': 'Framing labor', 'Type': 'Service'},
            request_id='stable-request-id',
        )

        self.assertEqual(result['Id'], '7')
        self.assertEqual(post.call_args.kwargs['params']['requestid'], 'stable-request-id')

    @patch('integrations.quickbooks.requests.get')
    def test_item_query_paginates_until_short_page(self, get):
        get.side_effect = [
            Mock(
                status_code=200,
                json=Mock(
                    return_value={'QueryResponse': {'Item': [item('1'), item('2')]}}
                ),
            ),
            Mock(
                status_code=200,
                json=Mock(return_value={'QueryResponse': {'Item': [item('3')]}}),
            ),
        ]

        items = list(
            QuickBooksAccountingClient().iter_items(self.connection, page_size=2)
        )

        self.assertEqual([entry['Id'] for entry in items], ['1', '2', '3'])
        first_query = get.call_args_list[0].kwargs['params']['query']
        self.assertIn('STARTPOSITION 1 MAXRESULTS 2', first_query)

    @patch('integrations.quickbooks.requests.get')
    def test_find_items_by_name_queries_exact_match(self, get):
        get.return_value = Mock(
            status_code=200,
            json=Mock(return_value={'QueryResponse': {'Item': [item()]}}),
        )

        matches = QuickBooksAccountingClient().find_items_by_name(
            self.connection, 'Framing labor'
        )

        self.assertEqual(matches[0]['Id'], '7')
        query = get.call_args.kwargs['params']['query']
        self.assertIn("Name = 'Framing labor'", query)


@override_settings(**SYNC_SETTINGS)
class QuickBooksItemSyncTests(TestCase):
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
        self.cost_code = CostCode.objects.create(
            organization=self.organization, code='06-100', name='Framing labor'
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

    def test_unmapped_cost_code_creates_item_and_durable_success(self):
        api = Mock()
        api.find_items_by_name.return_value = []
        api.create_item.return_value = item()

        attempt = start_cost_code_item_sync(
            cost_code_id=self.cost_code.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        self.assertEqual(attempt.external_id, '7')
        self.assertTrue(
            QuickBooksItemMapping.objects.filter(
                cost_code=self.cost_code,
                quickbooks_item_id='7',
            ).exists()
        )
        request_id = api.create_item.call_args.kwargs['request_id']
        self.assertEqual(request_id, attempt.request_id)

    def test_existing_name_is_matched_without_duplicate_create(self):
        api = Mock()
        api.find_items_by_name.return_value = [item()]

        attempt = start_cost_code_item_sync(
            cost_code_id=self.cost_code.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        api.create_item.assert_not_called()

    def test_mapped_item_refreshes_from_quickbooks(self):
        mapping = save_cost_code_item_mapping(
            cost_code=self.cost_code,
            connection=self.connection,
            item=item(),
            actor=self.admin,
        )
        api = Mock()
        api.get_item.return_value = item(name='Updated name', sync_token='4')

        attempt = start_cost_code_item_sync(
            cost_code_id=self.cost_code.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

        mapping.refresh_from_db()
        self.assertEqual(attempt.operation, QuickBooksSyncAttempt.Operation.READ)
        self.assertEqual(mapping.quickbooks_item_name, 'Updated name')
        self.assertEqual(mapping.quickbooks_sync_token, '4')

    def test_mapped_item_rejects_a_different_company_connection(self):
        save_cost_code_item_mapping(
            cost_code=self.cost_code,
            connection=self.connection,
            item=item(),
            actor=self.admin,
        )
        other_connection = QuickBooksConnection.objects.create(
            organization=self.organization,
            realm_id='67890',
            environment='sandbox',
            capabilities={'accounting_write': True},
            capabilities_checked_at=timezone.now(),
        )

        with self.assertRaisesMessage(QuickBooksSyncError, 'mapped QuickBooks company'):
            start_cost_code_item_sync(
                cost_code_id=self.cost_code.pk,
                connection_id=other_connection.pk,
                actor=self.admin,
                api_client=Mock(),
            )

    def test_missing_item_tombstones_mapping_as_success(self):
        mapping = save_cost_code_item_mapping(
            cost_code=self.cost_code,
            connection=self.connection,
            item=item(),
            actor=self.admin,
        )
        api = Mock()
        api.get_item.side_effect = QuickBooksAPIError(
            '610',
            'The requested QuickBooks record no longer exists.',
            status_code=400,
        )

        attempt = start_cost_code_item_sync(
            cost_code_id=self.cost_code.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

        mapping.refresh_from_db()
        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        self.assertEqual(mapping.status, QuickBooksItemMapping.Status.TOMBSTONED)
        self.assertEqual(mapping.last_synced_values['Id'], '7')

    def test_retryable_failure_records_backoff_without_secret_details(self):
        api = Mock()
        api.find_items_by_name.return_value = []
        api.create_item.side_effect = QuickBooksAPIError(
            'api_unavailable',
            'QuickBooks could not be reached. Try again later.',
            retryable=True,
        )

        attempt = start_cost_code_item_sync(
            cost_code_id=self.cost_code.pk,
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
        first_api.find_items_by_name.return_value = []
        first_api.create_item.side_effect = QuickBooksAPIError(
            'api_unavailable',
            'QuickBooks could not be reached.',
            retryable=True,
        )
        failed = start_cost_code_item_sync(
            cost_code_id=self.cost_code.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=first_api,
        )
        original_request_id = first_api.create_item.call_args.kwargs['request_id']
        retry_api = Mock()
        retry_api.find_items_by_name.return_value = []
        retry_api.create_item.return_value = item()

        succeeded = retry_item_sync_attempt(
            failed.pk,
            actor=self.admin,
            api_client=retry_api,
        )

        failed.refresh_from_db()
        self.assertEqual(succeeded.attempt_number, 2)
        self.assertEqual(
            retry_api.create_item.call_args.kwargs['request_id'],
            original_request_id,
        )
        self.assertEqual(failed.status, QuickBooksSyncAttempt.Status.RESOLVED)

    def test_read_only_company_records_nonretryable_failure(self):
        self.connection.capabilities = {'accounting_write': False}
        self.connection.save()

        attempt = start_cost_code_item_sync(
            cost_code_id=self.cost_code.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=Mock(),
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.FAILED)
        self.assertEqual(attempt.error_code, '6190')
        self.assertFalse(attempt.retryable)

    def test_running_item_sync_prevents_concurrent_duplicate(self):
        other_cost_code = CostCode.objects.create(
            organization=self.organization, code='02-200', name='Excavation'
        )
        QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            cost_code=other_cost_code,
            entity_type=QuickBooksSyncAttempt.EntityType.ITEM,
            operation=QuickBooksSyncAttempt.Operation.CREATE,
            direction=QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.RUNNING,
        )

        with self.assertRaises(QuickBooksSyncBusy):
            start_cost_code_item_sync(
                cost_code_id=self.cost_code.pk,
                connection_id=self.connection.pk,
                actor=self.admin,
                api_client=Mock(),
            )

    def test_failed_attempt_can_be_resolved_with_note(self):
        attempt = QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            cost_code=self.cost_code,
            entity_type=QuickBooksSyncAttempt.EntityType.ITEM,
            operation=QuickBooksSyncAttempt.Operation.CREATE,
            direction=QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.FAILED,
            error_code='6240',
            error_message='Duplicate name.',
        )

        attempt = resolve_item_sync_attempt(
            attempt.pk,
            actor=self.admin,
            note='Mapped the existing item instead.',
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.RESOLVED)
        self.assertEqual(attempt.resolved_by, self.admin)
        self.assertIn('Mapped', attempt.resolution_note)

    def test_execute_requires_running_attempt(self):
        attempt = QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            cost_code=self.cost_code,
            entity_type=QuickBooksSyncAttempt.EntityType.ITEM,
            operation=QuickBooksSyncAttempt.Operation.READ,
            direction=QuickBooksSyncAttempt.Direction.FROM_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.SUCCEEDED,
        )
        with self.assertRaises(QuickBooksSyncError):
            execute_item_sync_attempt(attempt.pk, api_client=Mock())

    @patch('integrations.views.start_cost_code_item_sync')
    def test_admin_can_start_sync_from_ui(self, start_sync):
        start_sync.return_value = QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            cost_code=self.cost_code,
            entity_type=QuickBooksSyncAttempt.EntityType.ITEM,
            operation=QuickBooksSyncAttempt.Operation.CREATE,
            direction=QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.SUCCEEDED,
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('integrations:quickbooks_cost_code_item_sync', args=(self.cost_code.pk,)),
            {'connection': self.connection.pk},
        )

        self.assertEqual(response.status_code, 302)
        start_sync.assert_called_once_with(
            cost_code_id=self.cost_code.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
        )

    def test_non_admin_cannot_start_sync(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('integrations:quickbooks_cost_code_item_sync', args=(self.cost_code.pk,)),
            {'connection': self.connection.pk},
        )
        self.assertEqual(response.status_code, 403)


@override_settings(**SYNC_SETTINGS)
class QuickBooksItemMappingServiceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(email='admin@example.com')
        self.organization = Organization.objects.create(name='Acme', slug='acme')
        self.other_organization = Organization.objects.create(name='Other', slug='other')
        self.cost_code = CostCode.objects.create(
            organization=self.organization, code='06-100', name='Framing labor'
        )
        self.connection = QuickBooksConnection.objects.create(
            organization=self.organization,
            realm_id='12345',
            environment='sandbox',
        )
        self.connection.set_tokens(access_token='access', refresh_token='refresh')
        self.connection.save()

    def test_save_rejects_mismatched_organization(self):
        other_connection = QuickBooksConnection.objects.create(
            organization=self.other_organization,
            realm_id='99999',
            environment='sandbox',
        )
        with self.assertRaises(QuickBooksMappingError):
            save_cost_code_item_mapping(
                cost_code=self.cost_code,
                connection=other_connection,
                item=item(),
                actor=self.admin,
            )

    def test_save_rejects_conflicting_active_mapping(self):
        other_cost_code = CostCode.objects.create(
            organization=self.organization, code='02-200', name='Excavation'
        )
        save_cost_code_item_mapping(
            cost_code=other_cost_code,
            connection=self.connection,
            item=item(item_id='7'),
            actor=self.admin,
        )
        with self.assertRaisesMessage(QuickBooksMappingError, 'already mapped'):
            save_cost_code_item_mapping(
                cost_code=self.cost_code,
                connection=self.connection,
                item=item(item_id='7'),
                actor=self.admin,
            )

    def test_unlink_preserves_history_and_allows_remapping(self):
        mapping = save_cost_code_item_mapping(
            cost_code=self.cost_code,
            connection=self.connection,
            item=item(),
            actor=self.admin,
        )
        unlink_cost_code_item_mapping(mapping.pk, actor=self.admin)
        mapping.refresh_from_db()
        self.assertEqual(mapping.status, QuickBooksItemMapping.Status.UNLINKED)

        other_cost_code = CostCode.objects.create(
            organization=self.organization, code='02-200', name='Excavation'
        )
        # Re-mapping the same external item to a different cost code succeeds
        # once the prior mapping is no longer active.
        new_mapping = save_cost_code_item_mapping(
            cost_code=other_cost_code,
            connection=self.connection,
            item=item(),
            actor=self.admin,
        )
        self.assertEqual(new_mapping.status, QuickBooksItemMapping.Status.ACTIVE)


@override_settings(**SYNC_SETTINGS)
class RetryQuickBooksSyncsCommandItemTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Acme', slug='acme')
        self.cost_code = CostCode.objects.create(
            organization=self.organization, code='06-100', name='Framing labor'
        )
        self.connection = QuickBooksConnection.objects.create(
            organization=self.organization,
            realm_id='12345',
            environment='sandbox',
        )
        self.connection.set_tokens(access_token='access', refresh_token='refresh')
        self.connection.save()

    @patch('integrations.management.commands.retry_quickbooks_syncs.retry_item_sync_attempt')
    def test_retry_command_processes_due_item_attempts(self, retry):
        attempt = QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            cost_code=self.cost_code,
            entity_type=QuickBooksSyncAttempt.EntityType.ITEM,
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
