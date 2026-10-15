from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Event
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.db import close_old_connections, connection, connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import Payment
from integrations.models import (
    QuickBooksConnection,
    QuickBooksInvoiceMapping,
    QuickBooksSyncAttempt,
)
from integrations.quickbooks import QuickBooksAPIError
from integrations.reconciliation import reconcile_due_payments
from integrations.services import save_invoice_mapping
from integrations.tests import test_payment_sync as fixtures
from projects.models import Organization


class ReconciliationFixtures:
    setUp = fixtures.QuickBooksInvoicePaymentSyncTests.setUp
    _issue_invoice = fixtures.QuickBooksInvoicePaymentSyncTests._issue_invoice

    def enable(self):
        QuickBooksConnection.objects.filter(pk=self.connection.pk).update(automatic_payment_updates=True)

    def due(self):
        QuickBooksInvoiceMapping.objects.update(next_payment_check_at=timezone.now() - timedelta(seconds=1))

    def api(self, *, balance='200.00', payments=None):
        api = Mock()
        api.get_invoice.return_value = {**fixtures.qb_invoice(), 'Balance': balance}
        api.find_payments_for_invoice.return_value = payments or []
        return api


@override_settings(**fixtures.SYNC_SETTINGS)
class ReconciliationTests(ReconciliationFixtures, TestCase):
    def test_opt_in_environment_and_connection_status_are_required(self):
        api = self.api()
        self.assertEqual(reconcile_due_payments(api_client=api), {})
        self.enable()
        for values in ({'status': 'disconnected'}, {'status': 'connected', 'environment': 'production'}):
            QuickBooksConnection.objects.filter(pk=self.connection.pk).update(**values)
            self.assertEqual(reconcile_due_payments(api_client=api), {})
        api.get_invoice.assert_not_called()

    def test_payment_import_and_repeat_are_idempotent_and_scheduled(self):
        self.enable()
        api = self.api(balance='100.00', payments=[fixtures.qb_payment()])
        api.get_payment.return_value = fixtures.qb_payment()
        result = reconcile_due_payments(api_client=api)[self.connection.pk]
        self.assertEqual(result['succeeded'], 1)
        self.invoice.refresh_from_db()
        self.assertEqual(str(self.invoice.balance_due), '100.00')
        self.assertIsNone(Payment.objects.get().recorded_by)
        self.assertEqual(reconcile_due_payments(api_client=api)[self.connection.pk]['succeeded'], 0)
        self.due()
        self.assertEqual(reconcile_due_payments(api_client=api)[self.connection.pk]['succeeded'], 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.invoice_mapping.refresh_from_db()
        self.assertIsNotNone(self.invoice_mapping.last_payment_check_at)
        self.assertEqual(str(self.invoice_mapping.external_balance), '100.00')
        api.create_invoice.assert_not_called()
        api.update_invoice.assert_not_called()

    def test_paid_invoice_reopens_after_remote_payment_deletion(self):
        self.enable()
        api = self.api(balance='0', payments=[fixtures.qb_payment(amount='200.00')])
        reconcile_due_payments(api_client=api)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, 'paid')
        self.due()
        api.get_invoice.return_value['Balance'] = '200.00'
        api.find_payments_for_invoice.return_value = []
        api.get_payment.side_effect = QuickBooksAPIError('610', 'Not found', status_code=404)
        self.assertEqual(reconcile_due_payments(api_client=api)[self.connection.pk]['succeeded'], 1)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, 'issued')
        self.assertEqual(str(self.invoice.balance_due), '200.00')

    def test_changed_total_requires_review_and_does_not_generate_repeated_failures(self):
        self.enable()
        api = self.api()
        api.get_invoice.return_value['TotalAmt'] = '250'
        self.assertEqual(reconcile_due_payments(api_client=api)[self.connection.pk]['failed'], 1)
        api.find_payments_for_invoice.assert_not_called()
        self.due()
        self.assertEqual(reconcile_due_payments(api_client=api)[self.connection.pk]['review'], 1)
        self.assertEqual(QuickBooksSyncAttempt.objects.count(), 1)
        api.get_invoice.assert_called_once()
        QuickBooksSyncAttempt.objects.update(status='resolved')
        api.get_invoice.return_value['TotalAmt'] = '200'
        self.due()
        self.assertEqual(reconcile_due_payments(api_client=api)[self.connection.pk]['succeeded'], 1)

    def test_balance_difference_is_visible_without_inventing_a_payment(self):
        self.enable()
        result = reconcile_due_payments(api_client=self.api(balance='100'))[self.connection.pk]
        self.assertEqual(result['unresolved'], 1)
        self.assertIn('balance differs', QuickBooksSyncAttempt.objects.get().error_message)
        self.assertFalse(Payment.objects.exists())

    def test_invalid_or_changed_remote_invoice_is_blocked_before_payment_import(self):
        self.enable()
        for changes in (
            {'Balance': 'NaN'}, {'TotalAmt': '0'}, {'Balance': '-1'},
            {'Balance': '1.005'}, {'TxnTaxDetail': {'TotalTax': '10'}},
            {'CustomerRef': {'value': 'different'}}, {'SyncToken': None},
            {'Id': 'different'},
        ):
            with self.subTest(changes=changes):
                QuickBooksSyncAttempt.objects.update(status='resolved')
                self.due()
                api = self.api(payments=[fixtures.qb_payment()])
                api.get_invoice.return_value.update(changes)
                self.assertEqual(reconcile_due_payments(api_client=api)[self.connection.pk]['failed'], 1)
                api.find_payments_for_invoice.assert_not_called()
        self.assertFalse(Payment.objects.exists())

    def test_disabling_during_scan_prevents_next_invoice_check(self):
        self.enable()
        second = self._issue_invoice()
        save_invoice_mapping(invoice=second, connection=self.connection,
                             quickbooks_invoice=fixtures.qb_invoice(invoice_id='10'), actor=self.admin)
        api = self.api()

        def read(*args):
            QuickBooksConnection.objects.filter(pk=self.connection.pk).update(automatic_payment_updates=False)
            return fixtures.qb_invoice()

        api.get_invoice.side_effect = read
        self.assertEqual(reconcile_due_payments(api_client=api)[self.connection.pk]['succeeded'], 1)
        self.assertEqual(QuickBooksSyncAttempt.objects.count(), 1)

    def test_transient_failure_waits_and_retries_same_operation(self):
        self.enable()
        api = self.api()
        api.get_invoice.side_effect = QuickBooksAPIError('unavailable', 'Unavailable', retryable=True)
        reconcile_due_payments(api_client=api)
        first = QuickBooksSyncAttempt.objects.get()
        self.due()
        self.assertEqual(reconcile_due_payments(api_client=api)[self.connection.pk]['deferred'], 1)
        self.assertEqual(api.get_invoice.call_count, 1)
        QuickBooksSyncAttempt.objects.update(next_retry_at=timezone.now() - timedelta(seconds=1))
        api.get_invoice.side_effect = None
        self.due()
        self.assertEqual(reconcile_due_payments(api_client=api)[self.connection.pk]['succeeded'], 1)
        self.assertEqual(QuickBooksSyncAttempt.objects.latest('pk').operation_key, first.operation_key)

    def test_oldest_due_invoice_is_not_starved_by_review_block(self):
        self.enable()
        api = self.api()
        api.get_invoice.return_value['TotalAmt'] = '250'
        reconcile_due_payments(api_client=api)
        second = self._issue_invoice()
        save_invoice_mapping(invoice=second, connection=self.connection,
                             quickbooks_invoice=fixtures.qb_invoice(invoice_id='10'), actor=self.admin)
        self.due()
        api.get_invoice.return_value = fixtures.qb_invoice(invoice_id='10')
        self.assertEqual(reconcile_due_payments(limit=1, api_client=api)[self.connection.pk]['review'], 1)
        self.assertEqual(reconcile_due_payments(limit=1, api_client=api)[self.connection.pk]['succeeded'], 1)

    def test_stale_attempt_recovered_once_and_fresh_attempt_left_alone(self):
        self.enable()
        attempt = QuickBooksSyncAttempt.objects.create(
            connection=self.connection, invoice=self.invoice, project=self.project,
            entity_type='payment', direction='from_quickbooks', operation='read', status='running',
            request_payload={'verify_invoice': True, 'quickbooks_invoice_id': '9'},
        )
        api = self.api()
        self.assertEqual(reconcile_due_payments(api_client=api)[self.connection.pk]['deferred'], 1)
        api.get_invoice.assert_not_called()
        QuickBooksSyncAttempt.objects.filter(pk=attempt.pk).update(started_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(reconcile_due_payments(api_client=api)[self.connection.pk]['succeeded'], 1)
        self.assertEqual(QuickBooksSyncAttempt.objects.count(), 1)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'succeeded')

    def test_toggle_requires_administrator_post_and_csrf(self):
        url = reverse('integrations:quickbooks_payment_updates', args=[self.connection.pk])
        for user in (self.staff, self.client_user):
            self.client.force_login(user)
            self.assertEqual(self.client.post(url, {'enabled': 'true'}).status_code, 403)
        self.client.force_login(self.admin)
        other = QuickBooksConnection.objects.create(
            organization=Organization.objects.create(name='Other', slug='other'),
            realm_id='other', environment='sandbox',
        )
        other_url = reverse('integrations:quickbooks_payment_updates', args=[other.pk])
        self.assertEqual(self.client.post(other_url, {'enabled': 'true'}).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {'enabled': 'bad'}).status_code, 400)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(url, {'enabled': 'true'}).status_code, 403)
        self.assertEqual(self.client.post(url, {'enabled': 'true'}).status_code, 302)
        self.connection.refresh_from_db()
        self.assertTrue(self.connection.automatic_payment_updates)
        self.assertTrue(self.connection.payment_worker_is_stale)
        response = self.client.get(reverse('integrations:quickbooks_connect'))
        self.assertContains(response, 'Payment worker has not checked in recently')
        reconcile_due_payments(api_client=self.api())
        self.connection.refresh_from_db()
        self.assertFalse(self.connection.payment_worker_is_stale)
        self.assertEqual(self.client.post(url, {'enabled': 'false'}).status_code, 302)

    def test_dry_run_does_not_call_api_or_write(self):
        self.enable()
        output = StringIO()
        with patch('integrations.reconciliation.QuickBooksAccountingClient') as api:
            call_command('reconcile_quickbooks_payments', dry_run=True, stdout=output)
        self.assertIn('1 mapped invoice(s) due', output.getvalue())
        api.assert_not_called()
        self.assertFalse(QuickBooksSyncAttempt.objects.exists())


@override_settings(**fixtures.SYNC_SETTINGS)
@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL session locks')
class ReconciliationConcurrencyTests(ReconciliationFixtures, TransactionTestCase):
    def test_two_workers_do_not_scan_the_same_company(self):
        self.enable()
        ready, release = Event(), Event()
        api = self.api()

        def read(*args):
            ready.set()
            self.assertTrue(release.wait(10))
            return fixtures.qb_invoice()

        api.get_invoice.side_effect = read

        def worker():
            close_old_connections()
            try:
                return reconcile_due_payments(api_client=api)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(worker)
            self.assertTrue(ready.wait(10))
            try:
                second = reconcile_due_payments(api_client=self.api())
                self.assertTrue(second[self.connection.pk]['busy'])
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10)[self.connection.pk]['succeeded'], 1)
        self.assertEqual(QuickBooksSyncAttempt.objects.count(), 1)
