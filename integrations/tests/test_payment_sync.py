from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice, InvoiceLineItem, Payment
from billing.services import issue_invoice, record_payment
from integrations.models import (
    QuickBooksConnection,
    QuickBooksCreditMemoMapping,
    QuickBooksPaymentMapping,
    QuickBooksProjectCustomerMapping,
    QuickBooksSyncAttempt,
)
from integrations.payment_sync import (
    QuickBooksSyncBusy,
    QuickBooksSyncError,
    resolve_invoice_payment_sync_attempt,
    retry_invoice_payment_sync_attempt,
    start_invoice_payment_sync,
)
from integrations.quickbooks import QuickBooksAccountingClient, QuickBooksAPIError
from integrations.services import save_invoice_mapping
from projects.models import (
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


def qb_invoice(invoice_id='9', doc_number='1001', customer_id='42', sync_token='0'):
    return {
        'Id': invoice_id,
        'SyncToken': sync_token,
        'DocNumber': doc_number,
        'CustomerRef': {'value': customer_id},
        'TotalAmt': '200.00',
        'Balance': '200.00',
        'TxnDate': '2026-08-01',
        'DueDate': '2026-08-31',
    }


def qb_payment(
    payment_id='55',
    amount='100.00',
    txn_date='2026-08-01',
    invoice_txn_id='9',
    customer_id='42',
    sync_token='0',
    ref_num='1001',
    method_name='Check',
):
    return {
        'Id': payment_id,
        'SyncToken': sync_token,
        'TotalAmt': amount,
        'TxnDate': txn_date,
        'PaymentRefNum': ref_num,
        'PaymentMethodRef': {'value': '1', 'name': method_name},
        'CustomerRef': {'value': customer_id},
        'Line': [
            {
                'Amount': amount,
                'LinkedTxn': [{'TxnId': invoice_txn_id, 'TxnType': 'Invoice'}],
            }
        ],
    }


def qb_credit_memo(
    credit_memo_id='77',
    amount='50.00',
    txn_date='2026-08-01',
    invoice_txn_id='9',
    customer_id='42',
    sync_token='0',
    doc_number='CM-1001',
):
    return {
        'Id': credit_memo_id,
        'SyncToken': sync_token,
        'TotalAmt': amount,
        'Balance': amount,
        'TxnDate': txn_date,
        'DocNumber': doc_number,
        'CustomerRef': {'value': customer_id},
        'Line': [
            {
                'Amount': amount,
                'LinkedTxn': [{'TxnId': invoice_txn_id, 'TxnType': 'Invoice'}],
            }
        ],
    }


@override_settings(**SYNC_SETTINGS)
class QuickBooksPaymentReadClientTests(TestCase):
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
    def test_get_payment_reads_by_id(self, get):
        get.return_value = Mock(
            status_code=200,
            json=Mock(return_value={'Payment': qb_payment()}),
        )

        result = QuickBooksAccountingClient().get_payment(self.connection, '55')

        self.assertEqual(result['Id'], '55')

    @patch('integrations.quickbooks.requests.get')
    def test_find_payments_for_invoice_filters_by_linked_txn(self, get):
        matching = qb_payment(payment_id='55', invoice_txn_id='9')
        other_invoice = qb_payment(payment_id='56', invoice_txn_id='99')
        get.return_value = Mock(
            status_code=200,
            json=Mock(
                return_value={'QueryResponse': {'Payment': [matching, other_invoice]}}
            ),
        )

        matches = QuickBooksAccountingClient().find_payments_for_invoice(
            self.connection, '42', '9'
        )

        self.assertEqual([entry['Id'] for entry in matches], ['55'])
        query = get.call_args.kwargs['params']['query']
        self.assertIn("CustomerRef = '42'", query)

    @patch('integrations.quickbooks.requests.get')
    def test_get_credit_memo_reads_by_id(self, get):
        get.return_value = Mock(
            status_code=200,
            json=Mock(return_value={'CreditMemo': qb_credit_memo()}),
        )

        result = QuickBooksAccountingClient().get_credit_memo(self.connection, '77')

        self.assertEqual(result['Id'], '77')

    @patch('integrations.quickbooks.requests.get')
    def test_find_credit_memos_for_invoice_filters_by_linked_txn(self, get):
        matching = qb_credit_memo(credit_memo_id='77', invoice_txn_id='9')
        other_invoice = qb_credit_memo(credit_memo_id='78', invoice_txn_id='99')
        get.return_value = Mock(
            status_code=200,
            json=Mock(
                return_value={'QueryResponse': {'CreditMemo': [matching, other_invoice]}}
            ),
        )

        matches = QuickBooksAccountingClient().find_credit_memos_for_invoice(
            self.connection, '42', '9'
        )

        self.assertEqual([entry['Id'] for entry in matches], ['77'])
        query = get.call_args.kwargs['params']['query']
        self.assertIn("CustomerRef = '42'", query)


@override_settings(**SYNC_SETTINGS)
class QuickBooksInvoicePaymentSyncTests(TestCase):
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
            quickbooks_customer_id='42',
            quickbooks_display_name='Smith Residence',
        )
        self.invoice = self._issue_invoice()
        self.invoice_mapping = save_invoice_mapping(
            invoice=self.invoice,
            connection=self.connection,
            quickbooks_invoice=qb_invoice(),
            actor=self.admin,
        )

    def _issue_invoice(self):
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
            quantity=Decimal('2.00'),
            unit_price=Decimal('100.00'),
        )
        line.full_clean()
        line.save()
        invoice.recalculate_totals()
        return issue_invoice(invoice_id=invoice.pk, actor=self.admin)

    def test_scan_imports_clean_payment_and_updates_invoice_balance(self):
        api = Mock()
        api.find_payments_for_invoice.return_value = [qb_payment()]
        api.find_credit_memos_for_invoice.return_value = []

        attempt = start_invoice_payment_sync(
            invoice_id=self.invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        self.assertEqual(attempt.response_snapshot['created'], ['55'])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PARTIALLY_PAID)
        self.assertEqual(self.invoice.amount_paid, Decimal('100.00'))
        payment = Payment.objects.get(invoice=self.invoice)
        self.assertEqual(payment.amount, Decimal('100.00'))
        self.assertEqual(payment.method, Payment.Method.CHECK)
        self.assertEqual(payment.reference, '1001')
        self.assertTrue(
            QuickBooksPaymentMapping.objects.filter(
                payment=payment, quickbooks_payment_id='55'
            ).exists()
        )

    def test_scan_flags_possible_duplicate_but_still_creates_other_clean_payments(self):
        manual_payment = record_payment(
            invoice_id=self.invoice.pk,
            actor=self.admin,
            amount=Decimal('100.00'),
            method=Payment.Method.CHECK,
            reference='1001',
            paid_date=date(2026, 8, 1),
            note='',
        )
        api = Mock()
        api.find_payments_for_invoice.return_value = [
            qb_payment(payment_id='55', amount='100.00', txn_date='2026-08-01'),
            qb_payment(payment_id='56', amount='100.00', txn_date='2026-08-05'),
        ]
        api.find_credit_memos_for_invoice.return_value = []

        attempt = start_invoice_payment_sync(
            invoice_id=self.invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.FAILED)
        self.assertFalse(attempt.retryable)
        snapshot = attempt.response_snapshot
        self.assertEqual(snapshot['created'], ['56'])
        self.assertEqual(
            snapshot['possible_duplicates'],
            [{'quickbooks_payment_id': '55', 'local_payment_id': manual_payment.pk}],
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal('200.00'))
        self.assertEqual(Payment.objects.filter(invoice=self.invoice).count(), 2)
        self.assertFalse(
            QuickBooksPaymentMapping.objects.filter(quickbooks_payment_id='55').exists()
        )

    def test_reverify_tombstones_missing_payment(self):
        api = Mock()
        api.find_payments_for_invoice.return_value = [qb_payment()]
        api.find_credit_memos_for_invoice.return_value = []
        start_invoice_payment_sync(
            invoice_id=self.invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )
        mapping = QuickBooksPaymentMapping.objects.get(quickbooks_payment_id='55')

        second_api = Mock()
        second_api.get_payment.side_effect = QuickBooksAPIError(
            '610',
            'The requested QuickBooks record no longer exists.',
            status_code=400,
        )
        second_api.find_payments_for_invoice.return_value = []
        second_api.find_credit_memos_for_invoice.return_value = []

        attempt = start_invoice_payment_sync(
            invoice_id=self.invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=second_api,
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        mapping.refresh_from_db()
        self.assertEqual(mapping.status, QuickBooksPaymentMapping.Status.TOMBSTONED)
        self.assertEqual(attempt.response_snapshot['reverified'][0]['status'], 'tombstoned')

    def test_reverify_marks_zero_amount_payment_as_voided(self):
        api = Mock()
        api.find_payments_for_invoice.return_value = [qb_payment()]
        api.find_credit_memos_for_invoice.return_value = []
        start_invoice_payment_sync(
            invoice_id=self.invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )
        mapping = QuickBooksPaymentMapping.objects.get(quickbooks_payment_id='55')

        second_api = Mock()
        second_api.get_payment.return_value = qb_payment(amount='0.00', sync_token='1')
        second_api.find_payments_for_invoice.return_value = []
        second_api.find_credit_memos_for_invoice.return_value = []

        start_invoice_payment_sync(
            invoice_id=self.invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=second_api,
        )

        mapping.refresh_from_db()
        self.assertEqual(mapping.status, QuickBooksPaymentMapping.Status.VOIDED)

    def test_scan_imports_credit_memo_and_updates_invoice_balance(self):
        api = Mock()
        api.find_payments_for_invoice.return_value = []
        api.find_credit_memos_for_invoice.return_value = [qb_credit_memo()]

        attempt = start_invoice_payment_sync(
            invoice_id=self.invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        self.assertEqual(attempt.response_snapshot['credit_memos_created'], ['77'])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PARTIALLY_PAID)
        self.assertEqual(self.invoice.amount_paid, Decimal('50.00'))
        payment = Payment.objects.get(invoice=self.invoice)
        self.assertEqual(payment.amount, Decimal('50.00'))
        self.assertEqual(payment.method, Payment.Method.CREDIT_MEMO)
        self.assertIsNone(payment.credit_memo)
        self.assertEqual(payment.reference, 'CM-1001')
        self.assertTrue(
            QuickBooksCreditMemoMapping.objects.filter(
                payment=payment, quickbooks_credit_memo_id='77'
            ).exists()
        )

    def test_scan_imports_both_payment_and_credit_memo_in_same_pass(self):
        api = Mock()
        api.find_payments_for_invoice.return_value = [qb_payment()]
        api.find_credit_memos_for_invoice.return_value = [qb_credit_memo()]

        attempt = start_invoice_payment_sync(
            invoice_id=self.invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        self.assertEqual(attempt.response_snapshot['created'], ['55'])
        self.assertEqual(attempt.response_snapshot['credit_memos_created'], ['77'])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal('150.00'))
        self.assertEqual(Payment.objects.filter(invoice=self.invoice).count(), 2)

    def test_credit_memo_reverify_tombstones_missing_credit_memo(self):
        api = Mock()
        api.find_payments_for_invoice.return_value = []
        api.find_credit_memos_for_invoice.return_value = [qb_credit_memo()]
        start_invoice_payment_sync(
            invoice_id=self.invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )
        mapping = QuickBooksCreditMemoMapping.objects.get(quickbooks_credit_memo_id='77')

        second_api = Mock()
        second_api.get_credit_memo.side_effect = QuickBooksAPIError(
            '610',
            'The requested QuickBooks record no longer exists.',
            status_code=400,
        )
        second_api.find_payments_for_invoice.return_value = []
        second_api.find_credit_memos_for_invoice.return_value = []

        attempt = start_invoice_payment_sync(
            invoice_id=self.invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=second_api,
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        mapping.refresh_from_db()
        self.assertEqual(mapping.status, QuickBooksCreditMemoMapping.Status.TOMBSTONED)
        self.assertEqual(
            attempt.response_snapshot['credit_memos_reverified'][0]['status'], 'tombstoned'
        )

    def test_duplicate_check_excludes_payments_already_linked_to_a_credit_memo_mapping(self):
        # An already-imported $50 credit memo (77) must not block a second, distinct $50
        # credit memo (78) with the same amount/date from importing cleanly — it should only
        # be compared against *unmapped* local payments when checking for duplicates.
        api = Mock()
        api.find_payments_for_invoice.return_value = []
        api.find_credit_memos_for_invoice.return_value = [qb_credit_memo()]
        start_invoice_payment_sync(
            invoice_id=self.invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

        second_api = Mock()
        second_api.find_payments_for_invoice.return_value = []
        second_api.get_credit_memo.return_value = qb_credit_memo(credit_memo_id='77')
        second_api.find_credit_memos_for_invoice.return_value = [
            qb_credit_memo(credit_memo_id='77'),
            qb_credit_memo(credit_memo_id='78', amount='50.00', txn_date='2026-08-01'),
        ]

        attempt = start_invoice_payment_sync(
            invoice_id=self.invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=second_api,
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        self.assertEqual(attempt.response_snapshot['credit_memos_created'], ['78'])
        self.assertEqual(attempt.response_snapshot['credit_memo_possible_duplicates'], [])
        self.assertEqual(Payment.objects.filter(invoice=self.invoice).count(), 2)

    def test_sync_requires_active_invoice_mapping(self):
        self.invoice_mapping.delete()

        with self.assertRaisesMessage(QuickBooksSyncError, 'Synchronize this invoice'):
            start_invoice_payment_sync(
                invoice_id=self.invoice.pk,
                connection_id=self.connection.pk,
                actor=self.admin,
                api_client=Mock(),
            )

    def test_retry_resolves_previous_attempt(self):
        first_api = Mock()
        first_api.find_payments_for_invoice.side_effect = QuickBooksAPIError(
            'api_unavailable',
            'QuickBooks could not be reached.',
            retryable=True,
        )
        failed = start_invoice_payment_sync(
            invoice_id=self.invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=first_api,
        )
        self.assertEqual(failed.status, QuickBooksSyncAttempt.Status.FAILED)
        self.assertTrue(failed.retryable)

        retry_api = Mock()
        retry_api.find_payments_for_invoice.return_value = [qb_payment()]
        retry_api.find_credit_memos_for_invoice.return_value = []

        succeeded = retry_invoice_payment_sync_attempt(
            failed.pk,
            actor=self.admin,
            api_client=retry_api,
        )

        failed.refresh_from_db()
        self.assertEqual(succeeded.attempt_number, 2)
        self.assertEqual(succeeded.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        self.assertEqual(failed.status, QuickBooksSyncAttempt.Status.RESOLVED)

    def test_failed_attempt_can_be_resolved_with_note(self):
        attempt = QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            invoice=self.invoice,
            entity_type=QuickBooksSyncAttempt.EntityType.PAYMENT,
            operation=QuickBooksSyncAttempt.Operation.READ,
            direction=QuickBooksSyncAttempt.Direction.FROM_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.FAILED,
            error_code='possible_duplicate_payment',
            error_message='Possible duplicate.',
        )

        attempt = resolve_invoice_payment_sync_attempt(
            attempt.pk,
            actor=self.admin,
            note='Confirmed this matches the manual entry.',
        )

        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.RESOLVED)
        self.assertEqual(attempt.resolved_by, self.admin)

    def test_running_payment_sync_prevents_concurrent_duplicate(self):
        other_invoice = self._issue_invoice()
        QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            invoice=other_invoice,
            entity_type=QuickBooksSyncAttempt.EntityType.PAYMENT,
            operation=QuickBooksSyncAttempt.Operation.READ,
            direction=QuickBooksSyncAttempt.Direction.FROM_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.RUNNING,
        )

        with self.assertRaises(QuickBooksSyncBusy):
            start_invoice_payment_sync(
                invoice_id=self.invoice.pk,
                connection_id=self.connection.pk,
                actor=self.admin,
                api_client=Mock(),
            )

    @patch('integrations.views.start_invoice_payment_sync')
    def test_admin_can_start_payment_sync_from_ui(self, start_sync):
        start_sync.return_value = QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            invoice=self.invoice,
            entity_type=QuickBooksSyncAttempt.EntityType.PAYMENT,
            operation=QuickBooksSyncAttempt.Operation.READ,
            direction=QuickBooksSyncAttempt.Direction.FROM_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.SUCCEEDED,
            response_snapshot={'created': [], 'reverified': [], 'possible_duplicates': []},
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('integrations:quickbooks_invoice_payment_sync', args=(self.invoice.pk,)),
            {'connection': self.connection.pk},
        )

        self.assertEqual(response.status_code, 302)
        start_sync.assert_called_once_with(
            invoice_id=self.invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
        )

    def test_non_admin_cannot_start_payment_sync(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('integrations:quickbooks_invoice_payment_sync', args=(self.invoice.pk,)),
            {'connection': self.connection.pk},
        )
        self.assertEqual(response.status_code, 403)


@override_settings(**SYNC_SETTINGS)
class RetryQuickBooksSyncsCommandPaymentTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(email='admin@example.com')
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
            subtotal_amount=Decimal('200.00'),
            total_amount=Decimal('200.00'),
        )

    @patch(
        'integrations.management.commands.retry_quickbooks_syncs.'
        'retry_invoice_payment_sync_attempt'
    )
    def test_retry_command_processes_due_payment_scan_attempts(self, retry):
        attempt = QuickBooksSyncAttempt.objects.create(
            connection=self.connection,
            invoice=self.invoice,
            entity_type=QuickBooksSyncAttempt.EntityType.PAYMENT,
            operation=QuickBooksSyncAttempt.Operation.READ,
            direction=QuickBooksSyncAttempt.Direction.FROM_QUICKBOOKS,
            status=QuickBooksSyncAttempt.Status.FAILED,
            retryable=True,
            next_retry_at=timezone.now() - timedelta(minutes=1),
        )
        retry.return_value = Mock(status=QuickBooksSyncAttempt.Status.SUCCEEDED)
        output = StringIO()

        call_command('retry_quickbooks_syncs', stdout=output)

        retry.assert_called_once_with(attempt.pk, actor=None)
        self.assertIn('1 succeeded', output.getvalue())
