"""Real parallel connections are exercised only by the PostgreSQL test settings."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from billing.models import Payment
from billing.services import record_payment, void_invoice
from integrations.customer_sync import QuickBooksSyncError
from integrations.invoice_sync import (
    execute_invoice_sync_attempt,
    retry_invoice_sync_attempt,
    start_invoice_sync,
)
from integrations.models import QuickBooksInvoiceMapping, QuickBooksSyncAttempt
from integrations.quickbooks import QuickBooksAPIError
from integrations.tests import test_invoice_sync as fixtures


@override_settings(**fixtures.SYNC_SETTINGS)
@skipUnless(connection.vendor == "postgresql", "Requires real PostgreSQL row locks")
class QuickBooksConcurrencyTests(TransactionTestCase):
    setUp = fixtures.QuickBooksInvoiceSyncTests.setUp
    create_issued_invoice = fixtures.QuickBooksInvoiceSyncTests.create_issued_invoice

    def worker(self, fn):
        close_old_connections()
        try:
            return fn()
        finally:
            connections.close_all()

    def sync(self, invoice, api):
        return start_invoice_sync(
            invoice_id=invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
        )

    def test_delayed_second_click_cannot_create_after_first_completes(self):
        invoice = self.create_issued_invoice()
        api = Mock()
        api.create_invoice.return_value = fixtures.qb_invoice()
        ready, release = Event(), Event()
        from integrations.invoice_sync import _build_invoice_line_items

        def pause(*args):
            payload = _build_invoice_line_items(*args)
            ready.set()
            self.assertTrue(release.wait(10))
            return payload

        with ThreadPoolExecutor(max_workers=1) as pool:
            with patch(
                "integrations.invoice_sync._build_invoice_line_items", side_effect=pause
            ):
                second = pool.submit(self.worker, lambda: self.sync(invoice, api))
                self.assertTrue(ready.wait(10))
            try:
                self.assertEqual(self.sync(invoice, api).status, "succeeded")
            finally:
                release.set()
            try:
                second.result(timeout=10)
            except Exception as exc:
                from integrations.customer_sync import QuickBooksSyncError

                self.assertIsInstance(exc, QuickBooksSyncError)
        self.assertEqual(api.create_invoice.call_count, 1)
        self.assertEqual(QuickBooksInvoiceMapping.objects.count(), 1)

    def test_new_click_cannot_bypass_unknown_create_result(self):
        invoice = self.create_issued_invoice()
        api = Mock()
        api.create_invoice.side_effect = QuickBooksAPIError(
            "api_unavailable", "Timeout", retryable=True
        )
        self.assertEqual(self.sync(invoice, api).status, "failed")
        api.create_invoice.side_effect = None
        api.create_invoice.return_value = fixtures.qb_invoice()
        from integrations.customer_sync import QuickBooksSyncError

        with self.assertRaises(QuickBooksSyncError):
            self.sync(invoice, api)
        self.assertEqual(api.create_invoice.call_count, 1)
        self.assertEqual(QuickBooksSyncAttempt.objects.count(), 1)

    def test_running_export_blocks_second_start_execution_and_local_void(self):
        invoice = self.create_issued_invoice()
        entered, release = Event(), Event()
        api = Mock()

        def create(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(10))
            return fixtures.qb_invoice()

        api.create_invoice.side_effect = create
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.worker, lambda: self.sync(invoice, api))
            self.assertTrue(entered.wait(10))
            try:
                attempt = QuickBooksSyncAttempt.objects.get(invoice=invoice)
                QuickBooksSyncAttempt.objects.filter(pk=attempt.pk).update(
                    last_execution_at=timezone.now() - timedelta(hours=1)
                )
                with self.assertRaises(QuickBooksSyncError):
                    self.sync(invoice, api)
                with self.assertRaises(QuickBooksSyncError):
                    execute_invoice_sync_attempt(
                        attempt.pk,
                        api_client=api,
                        recovery_before=timezone.now() - timedelta(minutes=15),
                    )
                with self.assertRaises(ValidationError):
                    void_invoice(
                        invoice_id=invoice.pk,
                        actor=self.admin,
                        reason="Concurrent void",
                    )
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10).status, "succeeded")
        self.assertEqual(api.create_invoice.call_count, 1)

    def test_competing_retries_create_one_successor(self):
        invoice = self.create_issued_invoice()
        api = Mock()
        api.create_invoice.side_effect = QuickBooksAPIError(
            "api_unavailable", "Timeout", retryable=True
        )
        failed = self.sync(invoice, api)
        api.create_invoice.side_effect = None
        api.create_invoice.return_value = fixtures.qb_invoice()

        def retry():
            try:
                return retry_invoice_sync_attempt(
                    failed.pk, actor=self.admin, api_client=api
                ).status
            except QuickBooksSyncError:
                return "busy"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.worker(retry), range(2)))
        self.assertCountEqual(results, ["succeeded", "busy"])
        self.assertEqual(
            api.create_invoice.call_count, 2
        )  # original timeout + one retry
        self.assertEqual(
            QuickBooksSyncAttempt.objects.filter(invoice=invoice).count(), 2
        )

    def test_concurrent_local_payments_cannot_overpay(self):
        invoice = self.create_issued_invoice()

        def pay():
            try:
                record_payment(
                    invoice_id=invoice.pk,
                    actor=self.admin,
                    amount="70",
                    method="check",
                    reference="",
                    paid_date=timezone.localdate(),
                    note="Concurrency test",
                )
                return "paid"
            except ValidationError:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.worker(pay), range(2)))
        self.assertCountEqual(results, ["paid", "rejected"])
        self.assertEqual(Payment.objects.filter(invoice=invoice).count(), 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice.balance_due, 30)

    def test_process_exit_releases_worker_lock_and_preserves_request(self):
        invoice = self.create_issued_invoice()
        # A real child process exits inside the API boundary, without finally blocks.
        # It uses only this test database and a fake API; no live credentials/calls.
        script = """
import json, os, sys
from unittest.mock import Mock
data = json.loads(sys.stdin.read())
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.test'
from django.conf import settings
settings.DATABASES = {'default': data['database']}
import django
django.setup()
from integrations.invoice_sync import start_invoice_sync
from django.contrib.auth import get_user_model
api = Mock()
def stop(connection, payload, *, request_id):
    print(request_id, flush=True)
    os._exit(73)
api.create_invoice.side_effect = stop
start_invoice_sync(invoice_id=data['invoice'], connection_id=data['connection'],
    actor=get_user_model().objects.get(pk=data['actor']), api_client=api)
"""
        child = subprocess.run(
            [sys.executable, "-c", script],
            input=json.dumps(
                {
                    "database": settings.DATABASES["default"],
                    "invoice": invoice.pk,
                    "connection": self.connection.pk,
                    "actor": self.admin.pk,
                }
            ),
            text=True,
            capture_output=True,
            timeout=20,
            env={**os.environ, "PYTHON_DOTENV_DISABLED": "1"},
        )
        self.assertEqual(child.returncode, 73, child.stderr)
        attempt = QuickBooksSyncAttempt.objects.get(invoice=invoice)
        self.assertEqual(attempt.status, "running")
        self.assertEqual(attempt.request_id, child.stdout.strip())
        self.assertIsNotNone(attempt.dispatched_at)
        QuickBooksSyncAttempt.objects.filter(pk=attempt.pk).update(
            last_execution_at=timezone.now() - timedelta(hours=1)
        )
        api = Mock()
        api.create_invoice.return_value = fixtures.qb_invoice()
        result = execute_invoice_sync_attempt(
            attempt.pk,
            api_client=api,
            recovery_before=timezone.now() - timedelta(minutes=15),
        )
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(
            api.create_invoice.call_args.kwargs["request_id"], child.stdout.strip()
        )

    def test_other_process_owns_execution_until_terminated(self):
        invoice = self.create_issued_invoice()
        with patch("integrations.invoice_sync.execute_invoice_sync_attempt"):
            self.sync(invoice, Mock())
        attempt = QuickBooksSyncAttempt.objects.get(invoice=invoice)
        script = """
import json, os, sys
data = json.loads(sys.stdin.readline())
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.test'
from django.conf import settings
settings.DATABASES = {'default': data['database']}
import django
django.setup()
from integrations.sync_execution import attempt_execution_lock
with attempt_execution_lock(data['attempt']):
    print('locked', flush=True)
    sys.stdin.readline()
"""
        child = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHON_DOTENV_DISABLED": "1"},
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                child.stdin.write(
                    json.dumps(
                        {
                            "database": settings.DATABASES["default"],
                            "attempt": attempt.pk,
                        }
                    )
                    + "\n"
                )
                child.stdin.flush()
                ready = pool.submit(child.stdout.readline)
                self.assertEqual(ready.result(timeout=15).strip(), "locked")
                api = Mock()
                with self.assertRaisesMessage(QuickBooksSyncError, "already executing"):
                    execute_invoice_sync_attempt(attempt.pk, api_client=api)
                api.create_invoice.assert_not_called()
            finally:
                child.kill()
                child.communicate(timeout=10)
        api.create_invoice.return_value = fixtures.qb_invoice()
        self.assertEqual(
            execute_invoice_sync_attempt(attempt.pk, api_client=api).status, "succeeded"
        )

    def test_overlapping_payment_imports_leave_one_ledger_entry(self):
        from integrations.payment_sync import start_invoice_payment_sync
        from integrations.services import save_invoice_mapping
        from integrations.tests.test_payment_sync import qb_payment

        invoice = self.create_issued_invoice()
        save_invoice_mapping(
            invoice=invoice,
            connection=self.connection,
            quickbooks_invoice=fixtures.qb_invoice(),
            actor=self.admin,
        )
        entered, release = Event(), Event()
        api = Mock()

        def find(*args):
            entered.set()
            self.assertTrue(release.wait(10))
            return [qb_payment(customer_id="55")]

        api.find_payments_for_invoice.side_effect = find

        def sync():
            return start_invoice_payment_sync(
                invoice_id=invoice.pk,
                connection_id=self.connection.pk,
                actor=self.admin,
                api_client=api,
            )

        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.worker, sync)
            self.assertTrue(entered.wait(10))
            try:
                with self.assertRaises(QuickBooksSyncError):
                    sync()
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10).status, "succeeded")
        self.assertEqual(Payment.objects.filter(invoice=invoice).count(), 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice.balance_due, 0)
