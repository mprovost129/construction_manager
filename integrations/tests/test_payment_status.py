from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from integrations.models import QuickBooksConnection, QuickBooksSyncAttempt
from integrations.payment_status import invoice_payment_status
from integrations.reconciliation import reconcile_due_payments
from integrations.tests.test_payment_sync import SYNC_SETTINGS
from integrations.tests.test_reconciliation import ReconciliationFixtures
from projects.models import Organization, OrganizationMembership, ProjectInternalAccess


@override_settings(**SYNC_SETTINGS)
class InvoicePaymentStatusTests(ReconciliationFixtures, TestCase):
    def status(self):
        self.invoice_mapping.refresh_from_db()
        return invoice_payment_status(self.invoice_mapping)

    def page(self, user=None):
        self.client.force_login(user or self.admin)
        return self.client.get(reverse('billing:invoice_detail', args=[self.project.pk, self.invoice.pk]))

    def attempt(self, **values):
        defaults = {
            'connection': self.connection, 'invoice': self.invoice, 'project': self.project,
            'entity_type': 'payment', 'direction': 'from_quickbooks', 'operation': 'read',
            'status': 'failed', 'error_message': 'Payment allocation needs review.',
            'request_payload': {'verify_invoice': True, 'quickbooks_invoice_id': '9'},
        }
        return QuickBooksSyncAttempt.objects.create(**{**defaults, **values})

    def test_refresh_or_unverified_manual_import_does_not_claim_a_balance_match(self):
        self.attempt(status='succeeded', request_payload={}, completed_at=timezone.now())
        self.assertIsNotNone(self.invoice_mapping.last_seen_at)
        self.assertIsNone(self.status()['last_verified_at'])
        self.assertContains(self.page(), 'No successful balance check has been recorded yet.')

    def test_verified_balance_is_shown_without_fetching_remote_data(self):
        self.enable()
        reconcile_due_payments(api_client=self.api())
        self.assertIsNotNone(self.status()['last_verified_at'])
        with patch('integrations.quickbooks.requests.get') as remote_get:
            response = self.page()
        remote_get.assert_not_called()
        self.assertContains(response, 'Balance last matched QuickBooks')
        self.assertContains(response, 'Payment checks scheduled')

    def test_a_new_failure_does_not_replace_the_last_successful_check_time(self):
        verified_at = timezone.now() - timedelta(hours=1)
        self.attempt(status='succeeded', completed_at=verified_at)
        self.attempt(completed_at=timezone.now())
        result = self.status()
        self.assertEqual(result['last_verified_at'], verified_at)
        self.assertEqual(result['label'], 'Payment check needs review')
        response = self.page()
        self.assertContains(response, 'Review payment check')
        self.assertContains(response, '?organization=acme#sync-errors-heading')

    def test_old_unresolved_failure_still_blocks_after_a_later_manual_success(self):
        self.attempt()
        self.attempt(status='succeeded', request_payload={}, completed_at=timezone.now())
        self.assertEqual(self.status()['label'], 'Payment check needs review')

    def test_retry_promise_requires_enabled_updates_and_an_eligible_automatic_failure(self):
        self.enable()
        failure = self.attempt(retryable=True, next_retry_at=timezone.now() + timedelta(minutes=1))
        self.assertIsNotNone(self.status()['retry_at'])
        self.assertTrue(self.status()['worker_stale'])
        self.assertContains(self.page(), 'automatic retries may be delayed')
        for changes in (
            {'attempt_number': 5},
            {'attempt_number': 1, 'request_payload': {}},
            {'request_payload': {'verify_invoice': True}, 'next_retry_at': None},
        ):
            QuickBooksSyncAttempt.objects.filter(pk=failure.pk).update(**changes)
            self.assertIsNone(self.status()['retry_at'])
        QuickBooksSyncAttempt.objects.filter(pk=failure.pk).update(next_retry_at=timezone.now())
        QuickBooksConnection.objects.filter(pk=self.connection.pk).update(automatic_payment_updates=False)
        self.assertIsNone(self.status()['retry_at'])

    def test_worker_and_connection_state_prevent_misleading_scheduled_status(self):
        self.assertEqual(self.status()['label'], 'Automatic payment checks paused')
        self.enable()
        self.assertEqual(self.status()['label'], 'Payment checks delayed')
        QuickBooksConnection.objects.filter(pk=self.connection.pk).update(payment_worker_seen_at=timezone.now())
        self.assertEqual(self.status()['label'], 'Payment check queued')
        QuickBooksConnection.objects.filter(pk=self.connection.pk).update(status='reauthorization_required')
        self.assertEqual(self.status()['label'], 'Reconnect QuickBooks')
        self.assertIsNone(self.status()['next_check_at'])

    def test_unconfigured_or_wrong_environment_cannot_claim_updates_are_scheduled(self):
        self.enable()
        with override_settings(QUICKBOOKS_CONFIGURED=False):
            self.assertEqual(self.status()['label'], 'Payment checks unavailable')
        with override_settings(QUICKBOOKS_ENVIRONMENT='production'):
            self.assertIsNone(self.status()['next_check_at'])
            self.assertEqual(self.status()['label'], 'Payment checks unavailable')

    def test_running_attempt_and_inactive_mapping_are_distinguished(self):
        self.attempt(status='running')
        self.assertEqual(self.status()['label'], 'Checking payments')
        self.invoice_mapping.status = 'voided'
        self.invoice_mapping.save(update_fields=['status'])
        self.assertEqual(self.status()['label'], 'Payment checks inactive')
        self.assertIsNone(self.status()['next_check_at'])

    def test_review_error_is_escaped_and_links_to_the_correct_invoice(self):
        self.attempt(error_message='<script>bad()</script>')
        response = self.page()
        self.assertNotContains(response, '<script>bad()</script>')
        self.assertContains(response, '&lt;script&gt;bad()&lt;/script&gt;')
        dashboard = self.client.get(reverse('integrations:quickbooks_connect'))
        self.assertContains(dashboard, reverse('billing:invoice_detail', args=[self.project.pk, self.invoice.pk]))

    def test_client_does_not_receive_internal_status_or_error_details(self):
        self.attempt()
        with patch('billing.views.invoice_payment_status') as status:
            response = self.page(self.client_user)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Payment allocation needs review')
        self.assertNotContains(response, 'Payment check status')
        status.assert_not_called()

    def test_project_manager_sees_status_without_company_admin_controls(self):
        membership = OrganizationMembership.objects.get(user=self.staff)
        ProjectInternalAccess.objects.create(project=self.project, membership=membership, can_manage=True)
        self.attempt()
        response = self.page(self.staff)
        self.assertContains(response, 'Payment check needs review')
        self.assertNotContains(response, 'Review payment check</a>')
        self.assertNotContains(response, 'Manage automatic updates</a>')

    def test_another_company_attempt_does_not_leak_into_invoice_status(self):
        other = QuickBooksConnection.objects.create(
            organization=Organization.objects.create(name='Other', slug='other'),
            realm_id='other', environment='sandbox',
        )
        self.attempt(connection=other, error_message='Other company private detail')
        self.assertEqual(self.status()['error_message'], '')
        self.assertNotContains(self.page(), 'Other company private detail')
