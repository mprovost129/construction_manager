from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice, InvoiceLineItem, Payment
from billing.services import delete_payment, issue_invoice, record_payment
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
from integrations.services import QuickBooksMappingError, save_invoice_mapping
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
                'DetailType': 'SalesItemLineDetail',
                'SalesItemLineDetail': {'ItemRef': {'value': '7'}},
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
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal('0.00'))
        self.assertEqual(self.invoice.balance_due, Decimal('200.00'))
        self.assertEqual(self.invoice.status, Invoice.Status.ISSUED)
        self.assertEqual(Payment.objects.count(), 1)

    def test_reverify_marks_zero_amount_payment_as_voided(self):
        api = Mock()
        api.find_payments_for_invoice.return_value = [qb_payment()]
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

        start_invoice_payment_sync(
            invoice_id=self.invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=second_api,
        )

        mapping.refresh_from_db()
        self.assertEqual(mapping.status, QuickBooksPaymentMapping.Status.VOIDED)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal('0.00'))
        self.assertEqual(self.invoice.status, Invoice.Status.ISSUED)
        self.client.force_login(self.client_user)
        response = self.client.get(reverse('billing:invoice_detail', args=(self.project.pk, self.invoice.pk)))
        self.assertContains(response, 'Reversed in QuickBooks')

    def sync_payments(self, records, *, existing=None, actor=None):
        api = Mock()
        api.find_payments_for_invoice.return_value = records
        if existing is not None:
            api.get_payment.side_effect = lambda connection, payment_id: existing[payment_id]
        attempt = start_invoice_payment_sync(
            invoice_id=self.invoice.pk,
            connection_id=self.connection.pk,
            actor=actor or self.admin,
            api_client=api,
        )
        self.invoice.refresh_from_db()
        return attempt

    def test_split_payment_uses_only_this_invoice_application(self):
        payment = qb_payment(amount='1000.00')
        payment['Line'] = [
            {'Amount': '75.00', 'LinkedTxn': [{'TxnId': '9', 'TxnType': 'Invoice'}]},
            {'Amount': '25.00', 'LinkedTxn': [{'TxnId': '9', 'TxnType': 'Invoice'}]},
            {'Amount': '800.00', 'LinkedTxn': [{'TxnId': '99', 'TxnType': 'Invoice'}]},
        ]
        payment['UnappliedAmt'] = '100.00'
        attempt = self.sync_payments([payment])
        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        self.assertEqual(self.invoice.amount_paid, Decimal('100.00'))
        self.assertEqual(self.invoice.balance_due, Decimal('100.00'))
        self.assertEqual(QuickBooksPaymentMapping.objects.get().external_amount, Decimal('100.00'))

    def test_zero_cash_credit_application_is_imported_once_as_payment(self):
        payment = qb_payment(amount='0.00')
        payment['Line'] = [
            {'Amount': '50.00', 'LinkedTxn': [{'TxnId': '9', 'TxnType': 'Invoice'}]},
            {'Amount': '50.00', 'LinkedTxn': [{'TxnId': '77', 'TxnType': 'CreditMemo'}]},
        ]
        for existing in (None, {'55': payment}):
            attempt = self.sync_payments([payment, payment], existing=existing)
            self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
            self.assertEqual(self.invoice.amount_paid, Decimal('50.00'))
            self.assertEqual(Payment.objects.count(), 1)
            self.assertEqual(Payment.objects.get().method, Payment.Method.CREDIT_MEMO)
            self.assertEqual(QuickBooksPaymentMapping.objects.get().status, 'active')
        self.assertEqual(QuickBooksCreditMemoMapping.objects.count(), 0)

    def test_changed_payment_reconciles_balance_and_date(self):
        self.sync_payments([qb_payment()])
        changed = qb_payment(amount='60.00', txn_date='2026-08-02', sync_token='1')
        attempt = self.sync_payments([changed], existing={'55': changed})
        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        self.assertEqual(self.invoice.amount_paid, Decimal('60.00'))
        self.assertEqual(self.invoice.balance_due, Decimal('140.00'))
        self.assertEqual(Payment.objects.get().paid_date, date(2026, 8, 2))

    def test_redistributed_receipts_reconcile_without_transient_overpayment(self):
        self.sync_payments([qb_payment(payment_id='55'), qb_payment(payment_id='56')])
        # The most recently synced mapping is read first, and its amount increases.
        updated = {'55': qb_payment(payment_id='55', amount='50.00'),
                   '56': qb_payment(payment_id='56', amount='150.00')}
        attempt = self.sync_payments(list(updated.values()), existing=updated)
        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        self.assertEqual(self.invoice.amount_paid, Decimal('200.00'))
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)
        self.assertEqual(Payment.objects.get(quickbooks_mapping__quickbooks_payment_id='56').amount, Decimal('150.00'))

    def test_true_overpayment_is_rejected_without_changing_existing_ledger(self):
        self.sync_payments([qb_payment()])
        changed = qb_payment(amount='250.00', sync_token='1')
        attempt = self.sync_payments([changed], existing={'55': changed})
        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.FAILED)
        self.assertEqual(self.invoice.amount_paid, Decimal('100.00'))
        self.assertEqual(Payment.objects.get().amount, Decimal('100.00'))
        self.assertEqual(QuickBooksPaymentMapping.objects.get().quickbooks_sync_token, '0')

    def test_mixed_cash_and_credit_counts_invoice_application_once(self):
        payment = qb_payment(amount='100.00')
        payment['Line'] = [
            {'Amount': '150.00', 'LinkedTxn': [{'TxnId': '9', 'TxnType': 'Invoice'}]},
            {'Amount': '50.00', 'LinkedTxn': [{'TxnId': '77', 'TxnType': 'CreditMemo'}]},
        ]
        attempt = self.sync_payments([payment])
        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        self.assertEqual(self.invoice.amount_paid, Decimal('150.00'))
        self.assertEqual(Payment.objects.count(), 1)

    def test_unapplied_payment_reverses_then_reapplication_restores_balance(self):
        original = qb_payment()
        self.sync_payments([original])
        unlinked = qb_payment(invoice_txn_id='99', sync_token='1')
        attempt = self.sync_payments([], existing={'55': unlinked})
        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        self.assertEqual(self.invoice.amount_paid, Decimal('0.00'))
        self.assertEqual(self.invoice.status, Invoice.Status.ISSUED)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertFalse(Payment.objects.get().is_applied)
        attempt = self.sync_payments([original], existing={'55': original})
        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        self.assertEqual(self.invoice.amount_paid, Decimal('100.00'))
        self.assertEqual(Payment.objects.count(), 1)

    def test_import_and_mapping_are_atomic(self):
        with patch('integrations.payment_sync.save_payment_mapping', side_effect=QuickBooksMappingError('Invalid mapping')):
            attempt = self.sync_payments([qb_payment()])
        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.FAILED)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(self.invoice.amount_paid, Decimal('0.00'))
        self.assertEqual(self.invoice.status, Invoice.Status.ISSUED)

    def test_invalid_or_ambiguous_payments_fail_without_posting(self):
        bad_records = []
        wrong_customer = qb_payment(customer_id='another-company')
        bad_records.append(wrong_customer)
        missing_date = qb_payment(txn_date='invalid')
        bad_records.append(missing_date)
        ambiguous = qb_payment()
        ambiguous['Line'][0]['LinkedTxn'].append({'TxnId': '99', 'TxnType': 'Invoice'})
        bad_records.append(ambiguous)
        for value in ('NaN', '-1.00', None, '1.001'):
            invalid_amount = qb_payment()
            invalid_amount['Line'][0]['Amount'] = value
            bad_records.append(invalid_amount)
        for payment in bad_records:
            with self.subTest(payment=payment):
                attempt = self.sync_payments([payment])
                self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.FAILED)
                self.assertEqual(self.invoice.amount_paid, Decimal('0.00'))
                self.assertEqual(Payment.objects.count(), 0)

    def test_imported_payment_cannot_be_deleted_locally(self):
        self.sync_payments([qb_payment()])
        payment = Payment.objects.get()
        with self.assertRaisesMessage(ValidationError, 'Update this payment in QuickBooks'):
            delete_payment(payment_id=payment.pk, actor=self.admin)
        self.client.force_login(self.admin)
        response = self.client.post(reverse('billing:payment_delete', args=(
            self.project.pk, self.invoice.pk, payment.pk,
        )), follow=True)
        self.assertContains(response, 'Update this payment in QuickBooks')
        self.assertContains(response, 'Managed in QuickBooks')
        self.assertEqual(Payment.objects.count(), 1)

    def test_background_retry_can_import_without_actor(self):
        api = Mock()
        api.find_payments_for_invoice.return_value = [qb_payment()]
        attempt = start_invoice_payment_sync(
            invoice_id=self.invoice.pk, connection_id=self.connection.pk,
            actor=None, api_client=api,
        )
        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
        self.assertEqual(Payment.objects.count(), 1)

    def test_legacy_credit_mapping_blocks_new_imports_for_review(self):
        local_payment = record_payment(
            invoice_id=self.invoice.pk, actor=self.admin, amount='50.00',
            method=Payment.Method.CREDIT_MEMO, reference='legacy',
            paid_date=date(2026, 8, 1), note='',
        )
        QuickBooksCreditMemoMapping.objects.create(
            payment=local_payment, invoice_mapping=self.invoice_mapping,
            connection=self.connection, quickbooks_credit_memo_id='77', quickbooks_sync_token='0',
        )
        attempt = self.sync_payments([qb_payment()])
        self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.FAILED)
        self.assertIn('Legacy QuickBooks credit imports', attempt.error_message)
        self.assertEqual(Payment.objects.count(), 1)

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
