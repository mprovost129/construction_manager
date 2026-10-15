"""Customer requests share the same exclusive execution boundary as invoices."""

from datetime import timedelta
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from integrations.customer_sync import (
    QuickBooksSyncBusy,
    QuickBooksSyncError,
    execute_customer_sync_attempt,
    start_project_customer_sync,
)
from integrations.models import QuickBooksSyncAttempt
from integrations.tests import test_customer_sync as fixtures
from integrations.tests.test_sync_recovery import WorkerStopped


class CustomerExecutionFixtures:
    setUp = fixtures.QuickBooksCustomerSyncTests.setUp

    def pending_attempt(self):
        with patch('integrations.customer_sync.execute_customer_sync_attempt'):
            start_project_customer_sync(
                project_id=self.project.pk,
                connection_id=self.connection.pk,
                actor=self.admin,
            )
        return QuickBooksSyncAttempt.objects.get(project=self.project)

    def api(self):
        api = Mock()
        api.find_customers_by_display_name.return_value = []
        api.create_customer.return_value = fixtures.customer()
        return api


@override_settings(**fixtures.SYNC_SETTINGS)
class CustomerExecutionTests(CustomerExecutionFixtures, TestCase):
    def test_reentrant_execution_is_blocked_without_failing_owner(self):
        attempt = self.pending_attempt()
        api, competing_api = self.api(), self.api()

        def find(*args):
            with self.assertRaises(QuickBooksSyncBusy):
                execute_customer_sync_attempt(attempt.pk, api_client=competing_api)
            return []

        api.find_customers_by_display_name.side_effect = find
        result = execute_customer_sync_attempt(attempt.pk, api_client=api)
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(result.execution_count, 1)
        self.assertIsNotNone(result.last_execution_at)
        self.assertEqual(competing_api.mock_calls, [])
        api.create_customer.assert_called_once()

        with self.assertRaises(QuickBooksSyncError):
            execute_customer_sync_attempt(attempt.pk, api_client=competing_api)
        self.assertEqual(competing_api.mock_calls, [])

    def test_unexpected_failure_is_saved_for_review_and_sanitized_in_ui(self):
        api = self.api()
        api.find_customers_by_display_name.side_effect = RuntimeError('private credential')
        self.client.force_login(self.admin)
        with patch('integrations.customer_sync.QuickBooksAccountingClient', return_value=api):
            response = self.client.post(
                reverse('integrations:quickbooks_customer_sync', args=[self.project.pk]),
                {'connection': self.connection.pk},
                follow=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'requires review')
        self.assertNotContains(response, 'private credential')
        attempt = QuickBooksSyncAttempt.objects.get(project=self.project)
        self.assertEqual(attempt.status, 'failed')
        self.assertEqual(attempt.error_code, 'execution_error')
        self.assertFalse(attempt.retryable)
        self.assertIsNone(attempt.next_retry_at)
        self.assertIsNotNone(attempt.completed_at)
        self.assertNotIn('private', attempt.error_message)
        self.assertEqual(attempt.request_payload, {'DisplayName': self.project.name})

    def test_interruption_releases_lock_and_reuses_saved_request(self):
        attempt = self.pending_attempt()
        api = self.api()
        api.create_customer.side_effect = WorkerStopped
        with self.assertRaises(WorkerStopped):
            execute_customer_sync_attempt(attempt.pk, api_client=api)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'running')
        original_call = api.create_customer.call_args
        api.create_customer.side_effect = None
        result = execute_customer_sync_attempt(attempt.pk, api_client=api)
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(result.execution_count, 2)
        self.assertEqual(api.create_customer.call_args, original_call)
        self.assertEqual(QuickBooksSyncAttempt.objects.count(), 1)

    def test_recent_recovery_is_blocked_before_api_calls(self):
        attempt = self.pending_attempt()
        api = self.api()
        with self.assertRaisesMessage(QuickBooksSyncBusy, 'too recent'):
            execute_customer_sync_attempt(
                attempt.pk,
                api_client=api,
                recovery_before=timezone.now() - timedelta(minutes=15),
            )
        self.assertEqual(api.mock_calls, [])
        attempt.refresh_from_db()
        self.assertEqual(attempt.execution_count, 0)

    def test_repeated_interruptions_stop_for_review(self):
        attempt = self.pending_attempt()
        QuickBooksSyncAttempt.objects.filter(pk=attempt.pk).update(execution_count=5)
        api = self.api()
        with self.assertRaisesMessage(QuickBooksSyncError, 'requires review'):
            execute_customer_sync_attempt(attempt.pk, api_client=api)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'failed')
        self.assertEqual(attempt.error_code, 'recovery_limit')
        self.assertFalse(attempt.retryable)
        self.assertEqual(api.mock_calls, [])


@override_settings(**fixtures.SYNC_SETTINGS)
@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL session locks')
class CustomerSessionLockTests(CustomerExecutionFixtures, TransactionTestCase):
    def test_independent_database_session_blocks_execution_until_released(self):
        attempt = self.pending_attempt()
        api = self.api()
        # An independent session bypasses the in-process mutex, exercising the
        # database lock used by separate application workers.
        owner = connection.Database.connect(**connection.get_connection_params())
        try:
            with owner.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_lock(%s)', [-((0x5142 << 48) + attempt.pk)])
            with self.assertRaises(QuickBooksSyncBusy):
                execute_customer_sync_attempt(attempt.pk, api_client=api)
            self.assertEqual(api.mock_calls, [])
            attempt.refresh_from_db()
            self.assertEqual(attempt.execution_count, 0)
            self.assertEqual(attempt.status, 'running')
        finally:
            owner.close()
        result = execute_customer_sync_attempt(attempt.pk, api_client=api)
        self.assertEqual(result.status, 'succeeded')
        self.assertEqual(result.execution_count, 1)
        api.create_customer.assert_called_once()
