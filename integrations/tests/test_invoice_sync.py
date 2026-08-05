from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice, InvoiceLineItem
from billing.services import issue_invoice, void_invoice
from integrations.invoice_sync import (
    QuickBooksSyncBusy,
    QuickBooksSyncError,
    execute_invoice_sync_attempt,
    resolve_invoice_sync_attempt,
    retry_invoice_sync_attempt,
    start_invoice_sync,
    start_invoice_void_sync,
)
from integrations.models import (
    QuickBooksConnection,
    QuickBooksInvoiceMapping,
    QuickBooksItemMapping,
    QuickBooksProjectCustomerMapping,
    QuickBooksSyncAttempt,
)
from integrations.quickbooks import QuickBooksAPIError
from integrations.services import (
    QuickBooksMappingError,
    refresh_invoice_mapping,
    save_invoice_mapping,
)
from projects.models import (
    CostCode,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
)

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


def qb_invoice(invoice_id='9', doc_number='1001', customer_id='55', sync_token='0'):
    return {
        'Id': invoice_id,
        'SyncToken': sync_token,
        'DocNumber': doc_number,
        'CustomerRef': {'value': customer_id},
        'TotalAmt': '100.00',
        'Balance': '100.00',
        'TxnDate': '2026-08-01',
        'DueDate': '2026-08-31',
    }


@override_settings(**SYNC_SETTINGS)
class QuickBooksInvoiceSyncTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(email='admin@example.com')
        self.staff = user_model.objects.create_user(email='staff@example.com')
        self.client_user = user_model.objects.create_user(email='client@example.com')
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
            created_by=self.admin,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.client_user,
            role=OrganizationMembership.Role.CLIENT,
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
        self.customer_mapping = QuickBooksProjectCustomerMapping.objects.create(
            project=self.project,
            connection=self.connection,
            quickbooks_customer_id='55',
            quickbooks_display_name='Smith Residence',
        )
        self.item_mapping = QuickBooksItemMapping.objects.create(
            cost_code=self.cost_code,
            connection=self.connection,
            quickbooks_item_id='7',
            quickbooks_item_name='Framing labor',
        )

    def create_issued_invoice(self, *, with_cost_code=True):
        invoice = Invoice.objects.create(
            organization=self.organization,
            project=self.project,
            title='Progress invoice',
            due_date=timezone.localdate() + timedelta(days=30),
            created_by=self.admin,
        )
        line = InvoiceLineItem(
            invoice=invoice,
            description='Framing labor',
            quantity=Decimal('1.00'),
            unit_price=Decimal('100.00'),
            cost_code=self.cost_code if with_cost_code else None,
        )
        line.full_clean()
        line.save()
        invoice.recalculate_totals()
        return issue_invoice(invoice_id=invoice.pk, actor=self.admin)

    def test_sync_creates_invoice_using_mapped_item(self):
        invoice = self.create_issued_invoice()
        api = Mock()
        api.create_invoice.return_value = qb_invoice()

        attempt = start_invoice_sync(
            invoice_id=invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        payload = api.create_invoice.call_args.args[1]
        self.assertEqual(payload['CustomerRef'], {'value': '55'})
        self.assertEqual(len(payload['Line']), 1)
        line = payload['Line'][0]
        self.assertEqual(line['SalesItemLineDetail']['ItemRef'], {'value': '7'})
        self.assertEqual(line['Amount'], '100.00')
        request_id = api.create_invoice.call_args.kwargs['request_id']
        self.assertEqual(request_id, attempt.request_id)
        self.assertTrue(
            QuickBooksInvoiceMapping.objects.filter(
                invoice=invoice, quickbooks_invoice_id='9'
            ).exists()
        )

    def test_sync_rejects_unmapped_line_items(self):
        invoice = self.create_issued_invoice(with_cost_code=False)

        with self.assertRaisesMessage(QuickBooksSyncError, 'Map every invoice line'):
            start_invoice_sync(
                invoice_id=invoice.pk,
                connection_id=self.connection.pk,
                actor=self.admin,
                api_client=Mock(),
            )

    def test_sync_rejects_draft_invoice(self):
        invoice = Invoice.objects.create(
            organization=self.organization,
            project=self.project,
            title='Draft invoice',
            due_date=timezone.localdate() + timedelta(days=30),
            created_by=self.admin,
        )
        with self.assertRaisesMessage(QuickBooksSyncError, 'Issue the invoice'):
            start_invoice_sync(
                invoice_id=invoice.pk,
                connection_id=self.connection.pk,
                actor=self.admin,
                api_client=Mock(),
            )

    def test_sync_rejects_voided_invoice(self):
        invoice = self.create_issued_invoice()
        void_invoice(invoice_id=invoice.pk, actor=self.admin, reason='Client canceled')

        with self.assertRaisesMessage(QuickBooksSyncError, 'voided invoice'):
            start_invoice_sync(
                invoice_id=invoice.pk,
                connection_id=self.connection.pk,
                actor=self.admin,
                api_client=Mock(),
            )

    def test_sync_requires_active_customer_mapping(self):
        self.customer_mapping.delete()
        invoice = self.create_issued_invoice()

        with self.assertRaisesMessage(QuickBooksSyncError, 'Map this project'):
            start_invoice_sync(
                invoice_id=invoice.pk,
                connection_id=self.connection.pk,
                actor=self.admin,
                api_client=Mock(),
            )

    def test_sync_rejects_already_mapped_invoice(self):
        invoice = self.create_issued_invoice()
        save_invoice_mapping(
            invoice=invoice,
            connection=self.connection,
            quickbooks_invoice=qb_invoice(),
            actor=self.admin,
        )

        with self.assertRaisesMessage(QuickBooksSyncError, 'already synchronized'):
            start_invoice_sync(
                invoice_id=invoice.pk,
                connection_id=self.connection.pk,
                actor=self.admin,
                api_client=Mock(),
            )

    def test_sync_rejects_mismatched_organization(self):
        other_organization = Organization.objects.create(name='Other', slug='other')
        other_connection = QuickBooksConnection.objects.create(
            organization=other_organization,
            realm_id='99999',
            environment='sandbox',
            capabilities={'accounting_write': True},
            capabilities_checked_at=timezone.now(),
        )
        invoice = self.create_issued_invoice()

        with self.assertRaisesMessage(QuickBooksSyncError, 'same company'):
            start_invoice_sync(
                invoice_id=invoice.pk,
                connection_id=other_connection.pk,
                actor=self.admin,
                api_client=Mock(),
            )

    def test_void_sync_rereads_for_fresh_sync_token(self):
        invoice = self.create_issued_invoice()
        mapping = save_invoice_mapping(
            invoice=invoice,
            connection=self.connection,
            quickbooks_invoice=qb_invoice(sync_token='0'),
            actor=self.admin,
        )
        void_invoice(invoice_id=invoice.pk, actor=self.admin, reason='Client canceled')
        api = Mock()
        api.get_invoice.return_value = qb_invoice(sync_token='3')
        api.void_invoice.return_value = qb_invoice(sync_token='4')

        attempt = start_invoice_void_sync(
            invoice_id=invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        void_payload = api.void_invoice.call_args.args[1]
        self.assertEqual(void_payload['SyncToken'], '3')
        mapping.refresh_from_db()
        self.assertEqual(mapping.status, QuickBooksInvoiceMapping.Status.VOIDED)

    def test_void_sync_requires_locally_voided_invoice(self):
        invoice = self.create_issued_invoice()
        save_invoice_mapping(
            invoice=invoice,
            connection=self.connection,
            quickbooks_invoice=qb_invoice(),
            actor=self.admin,
        )

        with self.assertRaisesMessage(QuickBooksSyncError, 'locally voided'):
            start_invoice_void_sync(
                invoice_id=invoice.pk,
                connection_id=self.connection.pk,
                actor=self.admin,
                api_client=Mock(),
            )

    def test_retryable_failure_records_backoff_without_secret_details(self):
        invoice = self.create_issued_invoice()
        api = Mock()
        api.create_invoice.side_effect = QuickBooksAPIError(
            'api_unavailable',
            'QuickBooks could not be reached. Try again later.',
            retryable=True,
        )

        attempt = start_invoice_sync(
            invoice_id=invoice.pk,
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
        invoice = self.create_issued_invoice()
        first_api = Mock()
        first_api.create_invoice.side_effect = QuickBooksAPIError(
            'api_unavailable',
            'QuickBooks could not be reached.',
            retryable=True,
        )
        failed = start_invoice_sync(
            invoice_id=invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=first_api,
        )
        original_request_id = first_api.create_invoice.call_args.kwargs['request_id']
        retry_api = Mock()
        retry_api.create_invoice.return_value = qb_invoice()

        succeeded = retry_invoice_sync_attempt(
            failed.pk,
            actor=self.admin,
            api_client=retry_api,
        )

        failed.refresh_from_db()
        self.assertEqual(succeeded.attempt_number, 2)
        self.assertEqual(
            retry_api.create_invoice.call_args.kwargs['request_id'],
            original_request_id,
        )
        self.assertEqual(failed.status, QuickBooksSyncAttempt.Status.RESOLVED)

    def test_read_only_company_records_nonretryable_failure(self):
        self.connection.capabilities = {'accounting_write': False}
        self.connection.save()
        invoice = self.create_issued_invoice()

        attempt = start_invoice_sync(
            invoice_id=invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=Mock(),
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.FAILED)
        self.assertEqual(attempt.error_code, '6190')
        self.assertFalse(attempt.retryable)

    def test_running_invoice_sync_prevents_concurrent_duplicate(self):
        invoice = self.create_issued_invoice()
        other_invoice = self.create_issued_invoice()
        QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            invoice=other_invoice,
            entity_type=QuickBooksSyncAttempt.EntityType.INVOICE,
            operation=QuickBooksSyncAttempt.Operation.CREATE,
            direction=QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.RUNNING,
        )

        with self.assertRaises(QuickBooksSyncBusy):
            start_invoice_sync(
                invoice_id=invoice.pk,
                connection_id=self.connection.pk,
                actor=self.admin,
                api_client=Mock(),
            )

    def test_failed_attempt_can_be_resolved_with_note(self):
        invoice = self.create_issued_invoice()
        attempt = QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            invoice=invoice,
            entity_type=QuickBooksSyncAttempt.EntityType.INVOICE,
            operation=QuickBooksSyncAttempt.Operation.CREATE,
            direction=QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.FAILED,
            error_code='6240',
            error_message='Duplicate document number.',
        )

        attempt = resolve_invoice_sync_attempt(
            attempt.pk,
            actor=self.admin,
            note='Reissued with a unique document number.',
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.RESOLVED)
        self.assertEqual(attempt.resolved_by, self.admin)
        self.assertIn('Reissued', attempt.resolution_note)

    def test_execute_requires_running_attempt(self):
        invoice = self.create_issued_invoice()
        attempt = QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            invoice=invoice,
            entity_type=QuickBooksSyncAttempt.EntityType.INVOICE,
            operation=QuickBooksSyncAttempt.Operation.CREATE,
            direction=QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.SUCCEEDED,
        )
        with self.assertRaises(QuickBooksSyncError):
            execute_invoice_sync_attempt(attempt.pk, api_client=Mock())

    @patch('integrations.views.start_invoice_sync')
    def test_admin_can_start_sync_from_ui(self, start_sync):
        invoice = self.create_issued_invoice()
        start_sync.return_value = QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            invoice=invoice,
            entity_type=QuickBooksSyncAttempt.EntityType.INVOICE,
            operation=QuickBooksSyncAttempt.Operation.CREATE,
            direction=QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.SUCCEEDED,
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('integrations:quickbooks_invoice_sync', args=(invoice.pk,)),
            {'connection': self.connection.pk},
        )

        self.assertEqual(response.status_code, 302)
        start_sync.assert_called_once_with(
            invoice_id=invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
        )

    def test_non_admin_cannot_start_sync(self):
        invoice = self.create_issued_invoice()
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('integrations:quickbooks_invoice_sync', args=(invoice.pk,)),
            {'connection': self.connection.pk},
        )
        self.assertEqual(response.status_code, 403)


@override_settings(**SYNC_SETTINGS)
class QuickBooksInvoiceMappingServiceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(email='admin@example.com')
        self.organization = Organization.objects.create(name='Acme', slug='acme')
        self.other_organization = Organization.objects.create(name='Other', slug='other')
        self.project = Project.objects.create(
            organization=self.organization,
            name='Smith Residence',
            created_by=self.admin,
        )
        self.connection = QuickBooksConnection.objects.create(
            organization=self.organization,
            realm_id='12345',
            environment='sandbox',
        )
        self.connection.set_tokens(access_token='access', refresh_token='refresh')
        self.connection.save()
        QuickBooksProjectCustomerMapping.objects.create(
            project=self.project,
            connection=self.connection,
            quickbooks_customer_id='55',
            quickbooks_display_name='Smith Residence',
        )
        self.invoice = self._issued_invoice(number=1)

    def _issued_invoice(self, *, number):
        return Invoice.objects.create(
            organization=self.organization,
            project=self.project,
            title=f'Invoice {number}',
            due_date=timezone.localdate() + timedelta(days=30),
            created_by=self.admin,
            status=Invoice.Status.ISSUED,
            issue_date=timezone.localdate(),
            issued_by=self.admin,
            issued_at=timezone.now(),
            number=number,
            subtotal_amount=Decimal('100.00'),
            total_amount=Decimal('100.00'),
        )

    def test_save_rejects_mismatched_organization(self):
        other_connection = QuickBooksConnection.objects.create(
            organization=self.other_organization,
            realm_id='99999',
            environment='sandbox',
        )
        with self.assertRaises(QuickBooksMappingError):
            save_invoice_mapping(
                invoice=self.invoice,
                connection=other_connection,
                quickbooks_invoice=qb_invoice(),
                actor=self.admin,
            )

    def test_save_rejects_conflicting_active_mapping(self):
        other_invoice = self._issued_invoice(number=2)
        save_invoice_mapping(
            invoice=other_invoice,
            connection=self.connection,
            quickbooks_invoice=qb_invoice(invoice_id='9'),
            actor=self.admin,
        )
        with self.assertRaisesMessage(QuickBooksMappingError, 'already mapped'):
            save_invoice_mapping(
                invoice=self.invoice,
                connection=self.connection,
                quickbooks_invoice=qb_invoice(invoice_id='9'),
                actor=self.admin,
            )

    def test_refresh_tombstones_on_missing_invoice(self):
        mapping = save_invoice_mapping(
            invoice=self.invoice,
            connection=self.connection,
            quickbooks_invoice=qb_invoice(),
            actor=self.admin,
        )
        api = Mock()
        api.get_invoice.side_effect = QuickBooksAPIError(
            '610',
            'The requested QuickBooks record no longer exists.',
            status_code=400,
        )

        result = refresh_invoice_mapping(mapping.pk, actor=self.admin, api_client=api)

        self.assertEqual(result.status, QuickBooksInvoiceMapping.Status.TOMBSTONED)


@override_settings(**SYNC_SETTINGS)
class RetryQuickBooksSyncsCommandInvoiceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(email='admin@example.com')
        self.organization = Organization.objects.create(name='Acme', slug='acme')
        self.project = Project.objects.create(
            organization=self.organization,
            name='Smith Residence',
            created_by=self.admin,
        )
        self.connection = QuickBooksConnection.objects.create(
            organization=self.organization,
            realm_id='12345',
            environment='sandbox',
        )
        self.connection.set_tokens(access_token='access', refresh_token='refresh')
        self.connection.save()
        self.invoice = Invoice.objects.create(
            organization=self.organization,
            project=self.project,
            title='Progress invoice',
            due_date=timezone.localdate() + timedelta(days=30),
            created_by=self.admin,
            status=Invoice.Status.ISSUED,
            issue_date=timezone.localdate(),
            issued_by=self.admin,
            issued_at=timezone.now(),
            number=1,
            subtotal_amount=Decimal('100.00'),
            total_amount=Decimal('100.00'),
        )

    @patch('integrations.management.commands.retry_quickbooks_syncs.retry_invoice_sync_attempt')
    def test_retry_command_processes_due_invoice_attempts(self, retry):
        attempt = QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            invoice=self.invoice,
            entity_type=QuickBooksSyncAttempt.EntityType.INVOICE,
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
