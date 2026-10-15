from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from billing.models import Payment
from integrations.customer_sync import QuickBooksSyncError
from integrations.invoice_sync import execute_invoice_sync_attempt, start_invoice_sync
from integrations.models import QuickBooksSyncAttempt
from integrations.payment_sync import (
    execute_invoice_payment_sync_attempt,
    start_invoice_payment_sync,
)
from integrations.quickbooks import QuickBooksAPIError
from integrations.services import save_invoice_mapping
from integrations.tests import test_invoice_sync as fixtures
from integrations.tests.test_payment_sync import qb_payment


class WorkerStopped(BaseException):
    """Simulate abrupt termination, bypassing ordinary exception handlers."""


@override_settings(**fixtures.SYNC_SETTINGS)
class QuickBooksRecoveryTests(TestCase):
    setUp = fixtures.QuickBooksInvoiceSyncTests.setUp
    create_issued_invoice = fixtures.QuickBooksInvoiceSyncTests.create_issued_invoice

    def sync(self, invoice, api):
        return start_invoice_sync(
            invoice_id=invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

    def stale(self, attempt):
        QuickBooksSyncAttempt.objects.filter(pk=attempt.pk).update(
            started_at=timezone.now() - timedelta(minutes=30),
            last_execution_at=timezone.now() - timedelta(minutes=30),
        )
        return timezone.now() - timedelta(minutes=15)

    def interrupted_invoice(self, *, checkpoint=False):
        invoice = self.create_issued_invoice()
        api = Mock()
        api.create_invoice.return_value = fixtures.qb_invoice()
        if checkpoint:
            with patch(
                "integrations.invoice_sync.save_invoice_mapping",
                side_effect=WorkerStopped,
            ):
                with self.assertRaises(WorkerStopped):
                    self.sync(invoice, api)
        else:
            api.create_invoice.side_effect = WorkerStopped
            with self.assertRaises(WorkerStopped):
                self.sync(invoice, api)
        attempt = QuickBooksSyncAttempt.objects.get(invoice=invoice)
        self.assertEqual(attempt.status, "running")
        self.assertIsNotNone(attempt.dispatched_at)
        return invoice, attempt, api

    def test_lost_response_recovers_same_payload_and_request(self):
        invoice, attempt, original_api = self.interrupted_invoice()
        api = Mock()
        api.create_invoice.return_value = fixtures.qb_invoice()
        result = execute_invoice_sync_attempt(
            attempt.pk, api_client=api, recovery_before=self.stale(attempt)
        )
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(
            api.create_invoice.call_args, original_api.create_invoice.call_args
        )
        self.assertEqual(
            QuickBooksSyncAttempt.objects.filter(invoice=invoice).count(), 1
        )
        self.assertEqual(result.execution_count, 2)

    def test_checkpoint_recovery_reads_remote_without_creating(self):
        _, attempt, _ = self.interrupted_invoice(checkpoint=True)
        self.assertEqual(attempt.external_id, "9")
        api = Mock()
        api.get_invoice.return_value = fixtures.qb_invoice()
        result = execute_invoice_sync_attempt(
            attempt.pk, api_client=api, recovery_before=self.stale(attempt)
        )
        self.assertEqual(result.status, "succeeded")
        api.create_invoice.assert_not_called()
        self.assertEqual(api.get_invoice.call_args.args[1], "9")

    def test_retry_after_checkpoint_read_failure_preserves_remote_identity(self):
        _, attempt, _ = self.interrupted_invoice(checkpoint=True)
        api = Mock()
        api.get_invoice.side_effect = QuickBooksAPIError(
            "api_unavailable", "Timeout", retryable=True
        )
        failed = execute_invoice_sync_attempt(
            attempt.pk, api_client=api, recovery_before=self.stale(attempt)
        )
        from integrations.invoice_sync import retry_invoice_sync_attempt

        api.get_invoice.side_effect = None
        api.get_invoice.return_value = fixtures.qb_invoice()
        result = retry_invoice_sync_attempt(failed.pk, actor=self.admin, api_client=api)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.external_id, "9")
        api.create_invoice.assert_not_called()

    def test_crash_after_mapping_commit_uses_saved_identity(self):
        invoice = self.create_issued_invoice()
        api = Mock()
        api.create_invoice.return_value = fixtures.qb_invoice()
        with patch(
            "integrations.invoice_sync._mark_attempt_succeeded",
            side_effect=WorkerStopped,
        ):
            with self.assertRaises(WorkerStopped):
                self.sync(invoice, api)
        attempt = QuickBooksSyncAttempt.objects.get(invoice=invoice)
        api.get_invoice.return_value = fixtures.qb_invoice()
        self.assertEqual(
            execute_invoice_sync_attempt(
                attempt.pk, api_client=api, recovery_before=self.stale(attempt)
            ).status,
            "succeeded",
        )
        self.assertEqual(api.create_invoice.call_count, 1)

    def test_unexpected_storage_error_is_sanitized_and_blocks_fresh_create(self):
        invoice = self.create_issued_invoice()
        api = Mock()
        api.create_invoice.return_value = fixtures.qb_invoice()
        with patch(
            "integrations.invoice_sync.save_invoice_mapping",
            side_effect=RuntimeError("private credential"),
        ):
            with self.assertRaises(QuickBooksSyncError):
                self.sync(invoice, api)
        attempt = QuickBooksSyncAttempt.objects.get(invoice=invoice)
        self.assertEqual(attempt.status, "failed")
        self.assertEqual(attempt.external_id, "9")
        self.assertNotIn("private", attempt.error_message)
        with self.assertRaises(QuickBooksSyncError):
            self.sync(invoice, api)
        self.assertEqual(api.create_invoice.call_count, 1)

    def test_resolving_uncertain_failure_does_not_allow_new_request(self):
        invoice = self.create_issued_invoice()
        api = Mock()
        api.create_invoice.side_effect = QuickBooksAPIError(
            "api_unavailable", "Timeout", retryable=True
        )
        attempt = self.sync(invoice, api)
        from integrations.invoice_sync import resolve_invoice_sync_attempt

        resolve_invoice_sync_attempt(
            attempt.pk, actor=self.admin, note="Needs further review"
        )
        with self.assertRaises(QuickBooksSyncError):
            self.sync(invoice, api)
        self.assertEqual(api.create_invoice.call_count, 1)

    def test_recent_attempt_is_not_recovered(self):
        _, attempt, _ = self.interrupted_invoice()
        api = Mock()
        with self.assertRaisesMessage(QuickBooksSyncError, "too recent"):
            execute_invoice_sync_attempt(
                attempt.pk,
                api_client=api,
                recovery_before=timezone.now() - timedelta(minutes=15),
            )
        api.create_invoice.assert_not_called()

    def test_recovery_limit_preserves_request_for_review(self):
        _, attempt, _ = self.interrupted_invoice()
        QuickBooksSyncAttempt.objects.filter(pk=attempt.pk).update(execution_count=5)
        api = Mock()
        with self.assertRaises(QuickBooksSyncError):
            execute_invoice_sync_attempt(
                attempt.pk, api_client=api, recovery_before=self.stale(attempt)
            )
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "failed")
        self.assertEqual(attempt.error_code, "recovery_limit")
        self.assertFalse(attempt.retryable)
        api.create_invoice.assert_not_called()

    def test_recovery_command_dry_run_and_repeat(self):
        _, attempt, _ = self.interrupted_invoice()
        self.stale(attempt)
        api = Mock()
        api.create_invoice.return_value = fixtures.qb_invoice()
        output = StringIO()
        with patch(
            "integrations.invoice_sync.QuickBooksAccountingClient", return_value=api
        ):
            call_command("recover_quickbooks_syncs", dry_run=True, stdout=output)
            api.create_invoice.assert_not_called()
            call_command("recover_quickbooks_syncs", stdout=output)
            call_command("recover_quickbooks_syncs", stdout=output)
        self.assertEqual(api.create_invoice.call_count, 1)
        self.assertIn("1 succeeded", output.getvalue())
        self.assertIn("0 succeeded", output.getvalue())

    def test_interrupted_void_recognizes_already_voided_remote_invoice(self):
        from billing.services import void_invoice
        from integrations.invoice_sync import start_invoice_void_sync

        invoice = self.create_issued_invoice()
        mapping = save_invoice_mapping(
            invoice=invoice,
            connection=self.connection,
            quickbooks_invoice=fixtures.qb_invoice(),
            actor=self.admin,
        )
        void_invoice(invoice_id=invoice.pk, actor=self.admin, reason="Recovery test")
        api = Mock()
        api.get_invoice.return_value = fixtures.qb_invoice()
        api.void_invoice.side_effect = WorkerStopped
        with self.assertRaises(WorkerStopped):
            start_invoice_void_sync(
                invoice_id=invoice.pk,
                connection_id=self.connection.pk,
                actor=self.admin,
                api_client=api,
            )
        attempt = QuickBooksSyncAttempt.objects.get(invoice=invoice)
        api.get_invoice.return_value = {
            **fixtures.qb_invoice(sync_token="1"),
            "TotalAmt": 0,
            "Balance": 0,
        }
        result = execute_invoice_sync_attempt(
            attempt.pk, api_client=api, recovery_before=self.stale(attempt)
        )
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(api.void_invoice.call_count, 1)
        mapping.refresh_from_db()
        self.assertEqual(mapping.status, "voided")

    def interrupted_payment(self, *, before_mapping):
        invoice = self.create_issued_invoice()
        save_invoice_mapping(
            invoice=invoice,
            connection=self.connection,
            quickbooks_invoice=fixtures.qb_invoice(),
            actor=self.admin,
        )
        api = Mock()
        payment = qb_payment(customer_id="55")
        api.find_payments_for_invoice.return_value = [payment]
        api.get_payment.return_value = payment
        target = "save_payment_mapping" if before_mapping else "_mark_attempt_succeeded"
        with patch(f"integrations.payment_sync.{target}", side_effect=WorkerStopped):
            with self.assertRaises(WorkerStopped):
                start_invoice_payment_sync(
                    invoice_id=invoice.pk,
                    connection_id=self.connection.pk,
                    actor=self.admin,
                    api_client=api,
                )
        attempt = QuickBooksSyncAttempt.objects.get(invoice=invoice)
        self.assertEqual(
            Payment.objects.filter(invoice=invoice).count(), 0 if before_mapping else 1
        )
        result = execute_invoice_payment_sync_attempt(
            attempt.pk, api_client=api, recovery_before=self.stale(attempt)
        )
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(Payment.objects.filter(invoice=invoice).count(), 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice.balance_due, 0)

    def test_payment_crash_rolls_back_unmapped_ledger_entry(self):
        self.interrupted_payment(before_mapping=True)

    def test_payment_crash_after_commit_does_not_duplicate(self):
        self.interrupted_payment(before_mapping=False)
