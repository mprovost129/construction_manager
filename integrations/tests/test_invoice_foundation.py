from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice, InvoiceLineItem
from billing.services import issue_invoice
from integrations.invoice_mapping import save_invoice_mapping
from integrations.models import (
    QuickBooksConnection,
    QuickBooksProjectCustomerMapping,
)
from integrations.quickbooks import QuickBooksAccountingClient, QuickBooksAPIError
from integrations.services import QuickBooksMappingError
from projects.models import (
    ActivityEvent,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
)

TEST_KEY = Fernet.generate_key().decode()
QBO_SETTINGS = {
    'QUICKBOOKS_CONFIGURED': True,
    'QUICKBOOKS_ENVIRONMENT': 'sandbox',
    'QUICKBOOKS_CLIENT_ID': 'client-id',
    'QUICKBOOKS_CLIENT_SECRET': 'client-secret',
    'QUICKBOOKS_REDIRECT_URI': 'https://example.com/callback/',
    'QUICKBOOKS_TOKEN_ENCRYPTION_KEYS': (TEST_KEY,),
    'QUICKBOOKS_MINOR_VERSION': 75,
}


def external_invoice(invoice_id='84', *, customer_id='42', sync_token='3'):
    return {
        'Id': invoice_id,
        'SyncToken': sync_token,
        'DocNumber': 'QBO-1042',
        'TxnDate': '2026-08-01',
        'DueDate': '2026-08-31',
        'CustomerRef': {'value': customer_id, 'name': 'Smith Residence'},
        'CurrencyRef': {'value': 'USD', 'name': 'United States Dollar'},
        'TotalAmt': 100.0,
        'Balance': 75.0,
        'LinkedTxn': [{'TxnId': '91', 'TxnType': 'Payment'}],
    }


@override_settings(**QBO_SETTINGS)
class QuickBooksInvoiceClientTests(TestCase):
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
    def test_get_invoice_reads_encoded_identity(self, get):
        get.return_value = Mock(
            status_code=200,
            json=Mock(return_value={'Invoice': external_invoice('84/5')}),
        )

        result = QuickBooksAccountingClient().get_invoice(self.connection, '84/5')

        self.assertEqual(result['Id'], '84/5')
        self.assertIn('/invoice/84%2F5', get.call_args.args[0])

    @patch('integrations.quickbooks.requests.post')
    def test_create_invoice_uses_customer_lines_and_stable_request_id(self, post):
        payload = {
            'CustomerRef': {'value': '42'},
            'Line': [
                {
                    'DetailType': 'SalesItemLineDetail',
                    'Amount': 100.0,
                    'SalesItemLineDetail': {'ItemRef': {'value': '1'}},
                }
            ],
        }
        post.return_value = Mock(
            status_code=200,
            json=Mock(return_value={'Invoice': external_invoice()}),
        )

        result = QuickBooksAccountingClient().create_invoice(
            self.connection,
            payload,
            request_id='stable-invoice-request',
        )

        self.assertEqual(result['Id'], '84')
        self.assertEqual(post.call_args.kwargs['json'], payload)
        self.assertEqual(
            post.call_args.kwargs['params'],
            {'minorversion': 75, 'requestid': 'stable-invoice-request'},
        )

    @patch('integrations.quickbooks.requests.post')
    def test_create_invoice_rejects_missing_customer_or_lines_before_api_call(self, post):
        client = QuickBooksAccountingClient()

        with self.assertRaisesMessage(QuickBooksAPIError, 'mapped customer'):
            client.create_invoice(
                self.connection,
                {'Line': [{}]},
                request_id='stable-request',
            )
        with self.assertRaisesMessage(QuickBooksAPIError, 'line item'):
            client.create_invoice(
                self.connection,
                {'CustomerRef': {'value': '42'}, 'Line': []},
                request_id='stable-request',
            )
        post.assert_not_called()

    @patch('integrations.quickbooks.requests.post')
    def test_update_invoice_is_sparse_and_requires_sync_identity(self, post):
        post.return_value = Mock(
            status_code=200,
            json=Mock(return_value={'Invoice': external_invoice(sync_token='4')}),
        )
        client = QuickBooksAccountingClient()

        client.update_invoice(
            self.connection,
            {'Id': '84', 'SyncToken': '3', 'DueDate': '2026-09-15'},
            request_id='update-request',
        )

        self.assertTrue(post.call_args.kwargs['json']['sparse'])
        with self.assertRaisesMessage(QuickBooksAPIError, 'latest sync token'):
            client.update_invoice(
                self.connection,
                {'Id': '84'},
                request_id='update-request',
            )

    @patch('integrations.quickbooks.requests.post')
    def test_void_invoice_sends_only_current_identity_and_operation(self, post):
        post.return_value = Mock(
            status_code=200,
            json=Mock(return_value={'Invoice': external_invoice(sync_token='4')}),
        )

        QuickBooksAccountingClient().void_invoice(
            self.connection,
            {'Id': '84', 'SyncToken': '3', 'TotalAmt': 100},
            request_id='void-request',
        )

        self.assertEqual(
            post.call_args.kwargs['json'],
            {'Id': '84', 'SyncToken': '3'},
        )
        self.assertEqual(
            post.call_args.kwargs['params'],
            {
                'minorversion': 75,
                'operation': 'void',
                'requestid': 'void-request',
            },
        )

    @patch('integrations.quickbooks.requests.post')
    def test_all_invoice_writes_require_request_id(self, post):
        with self.assertRaisesMessage(QuickBooksAPIError, 'stable request ID'):
            QuickBooksAccountingClient().create_invoice(
                self.connection,
                {'CustomerRef': {'value': '42'}, 'Line': [{}]},
                request_id='',
            )
        post.assert_not_called()


@override_settings(**QBO_SETTINGS)
class QuickBooksInvoiceMappingTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(email='admin@example.com')
        self.client_user = user_model.objects.create_user(email='client@example.com')
        self.organization = Organization.objects.create(name='Acme', slug='acme')
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.admin,
            role=OrganizationMembership.Role.ADMIN,
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
            company_name='Acme QuickBooks',
        )
        QuickBooksProjectCustomerMapping.objects.create(
            project=self.project,
            connection=self.connection,
            quickbooks_customer_id='42',
            quickbooks_sync_token='2',
            quickbooks_display_name='Smith Residence',
        )

    def create_draft(self, title='Progress invoice'):
        invoice = Invoice.objects.create(
            organization=self.organization,
            project=self.project,
            title=title,
            due_date=timezone.localdate() + timedelta(days=30),
            created_by=self.admin,
        )
        InvoiceLineItem.objects.create(
            invoice=invoice,
            description='Construction services',
            quantity=Decimal('1.00'),
            unit_price=Decimal('100.00'),
        )
        invoice.recalculate_totals()
        return invoice

    def create_issued(self, title='Progress invoice'):
        invoice = self.create_draft(title)
        return issue_invoice(invoice_id=invoice.pk, actor=self.admin)

    def test_mapping_saves_identity_balances_snapshot_and_audit(self):
        invoice = self.create_issued()

        mapping = save_invoice_mapping(
            invoice=invoice,
            connection=self.connection,
            quickbooks_invoice=external_invoice(),
            actor=self.admin,
        )

        self.assertEqual(mapping.quickbooks_invoice_id, '84')
        self.assertEqual(mapping.quickbooks_sync_token, '3')
        self.assertEqual(mapping.external_total_amount, Decimal('100.00'))
        self.assertEqual(mapping.external_balance, Decimal('75.00'))
        self.assertEqual(mapping.currency_code, 'USD')
        self.assertEqual(mapping.last_synced_values['LinkedTxn'][0]['TxnType'], 'Payment')
        self.assertTrue(
            ActivityEvent.objects.filter(
                project=self.project,
                event_type=ActivityEvent.Type.QUICKBOOKS_INVOICE_MAPPED,
            ).exists()
        )

    def test_mapping_requires_issued_invoice_and_matching_customer(self):
        draft = self.create_draft()
        with self.assertRaisesMessage(QuickBooksMappingError, 'Issue the local invoice'):
            save_invoice_mapping(
                invoice=draft,
                connection=self.connection,
                quickbooks_invoice=external_invoice(),
                actor=self.admin,
            )

        invoice = issue_invoice(invoice_id=draft.pk, actor=self.admin)
        with self.assertRaisesMessage(QuickBooksMappingError, 'does not match'):
            save_invoice_mapping(
                invoice=invoice,
                connection=self.connection,
                quickbooks_invoice=external_invoice(customer_id='99'),
                actor=self.admin,
            )

    def test_external_invoice_cannot_map_to_two_local_invoices(self):
        first = self.create_issued('First invoice')
        second = self.create_issued('Second invoice')
        save_invoice_mapping(
            invoice=first,
            connection=self.connection,
            quickbooks_invoice=external_invoice(),
            actor=self.admin,
        )

        with self.assertRaisesMessage(QuickBooksMappingError, 'already mapped'):
            save_invoice_mapping(
                invoice=second,
                connection=self.connection,
                quickbooks_invoice=external_invoice(),
                actor=self.admin,
            )

    def test_mapping_identity_is_immutable(self):
        mapping = save_invoice_mapping(
            invoice=self.create_issued(),
            connection=self.connection,
            quickbooks_invoice=external_invoice(),
            actor=self.admin,
        )
        mapping.quickbooks_invoice_id = '999'

        with self.assertRaisesMessage(ValidationError, 'identity is immutable'):
            mapping.save()

    def test_staff_invoice_page_shows_mapping_but_client_page_does_not(self):
        invoice = self.create_issued()
        save_invoice_mapping(
            invoice=invoice,
            connection=self.connection,
            quickbooks_invoice=external_invoice(),
            actor=self.admin,
        )
        detail_url = reverse(
            'billing:invoice_detail',
            args=(self.project.pk, invoice.pk),
        )

        self.client.force_login(self.admin)
        response = self.client.get(detail_url)
        self.assertContains(response, 'Accounting identity')
        self.assertContains(response, 'QBO-1042')

        self.client.force_login(self.client_user)
        response = self.client.get(detail_url)
        self.assertNotContains(response, 'Accounting identity')
        self.assertNotContains(response, 'QBO-1042')
