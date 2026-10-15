"""Pilot journey through real approval views and simulated QuickBooks responses."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice, Payment
from billing.services import (
    create_invoice_from_change_order,
    create_invoice_from_selection,
)
from integrations.invoice_sync import start_invoice_sync
from integrations.models import (
    QuickBooksConnection,
    QuickBooksItemMapping,
    QuickBooksProjectCustomerMapping,
    QuickBooksSyncAttempt,
)
from integrations.payment_sync import start_invoice_payment_sync
from projects.financials import project_financial_summary
from projects.models import (
    ChangeOrder,
    ChangeOrderLineItem,
    CostCode,
    Estimate,
    EstimateLineItem,
    FinishSelection,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
    SelectionOption,
)


@override_settings(QUICKBOOKS_TOKEN_ENCRYPTION_KEYS=(Fernet.generate_key().decode(),))
class PilotWorkflowTests(TestCase):
    def post_as(self, user, route, args, data=None):
        self.client.force_login(user)
        response = self.client.post(reverse(route, args=args), data or {})
        self.assertEqual(response.status_code, 302)

    def test_pricing_selection_overage_invoice_sync_and_settlement(self):
        admin = get_user_model().objects.create_user(email='builder@example.com')
        homeowner = get_user_model().objects.create_user(email='homeowner@example.com')
        company = Organization.objects.create(name='Pilot Builder', slug='pilot')
        OrganizationMembership.objects.create(
            organization=company,
            user=admin,
            role=OrganizationMembership.Role.ADMIN,
        )
        project = Project.objects.create(
            organization=company, name='Pilot Residence', created_by=admin
        )
        ProjectMembership.objects.create(
            project=project,
            user=homeowner,
            role=OrganizationMembership.Role.CLIENT,
        )
        code = CostCode.objects.create(organization=company, code='TILE', name='Tile')
        estimate = Estimate.objects.create(
            project=project, number=1, title='Base home', created_by=admin
        )
        EstimateLineItem.objects.create(
            estimate=estimate,
            description='Home including tile allowance',
            quantity=1,
            unit_price=Decimal('100000.00'),
            unit_cost=Decimal('70000.00'),
        )
        estimate.recalculate_from_line_items()
        self.post_as(admin, 'projects:estimate_submit', (project.pk, estimate.pk))
        self.post_as(
            homeowner,
            'projects:estimate_decision',
            (project.pk, estimate.pk),
            {'decision': 'approved'},
        )
        project.refresh_from_db()
        self.assertEqual(project.contract_amount, Decimal('100000.00'))

        selection = FinishSelection.objects.create(
            project=project,
            number=1,
            title='Bathroom tile',
            description='Choose tile',
            allowance_amount=Decimal('5000.00'),
            created_by=admin,
            status=FinishSelection.Status.OPEN,
            opened_by=admin,
            opened_at=timezone.now(),
        )
        option = SelectionOption.objects.create(
            selection=selection,
            name='Upgraded tile',
            price=Decimal('5800.00'),
            cost=Decimal('4000.00'),
        )
        self.post_as(
            homeowner,
            'projects:selection_choose',
            (project.pk, selection.pk),
            {'option': option.pk},
        )
        selection.refresh_from_db()
        self.assertTrue(selection.requires_change_order)
        self.assertEqual(project.change_orders.count(), 0)

        change = ChangeOrder.objects.create(
            project=project,
            number=1,
            title='Tile overage',
            description='Approved upgrade',
            created_by=admin,
        )
        ChangeOrderLineItem.objects.create(
            change_order=change,
            category='material',
            cost_code=code,
            description='Tile over allowance',
            quantity=1,
            unit_price=Decimal('800.00'),
            unit_cost=Decimal('500.00'),
        )
        change.recalculate_from_line_items()
        self.post_as(admin, 'projects:change_order_submit', (project.pk, change.pk))
        self.post_as(
            homeowner,
            'projects:change_order_decision',
            (project.pk, change.pk),
            {'decision': 'approved'},
        )
        change.refresh_from_db()
        self.assertEqual(change.status, ChangeOrder.Status.APPROVED)

        form_data = {
            'title': 'Tile',
            'due_date': timezone.localdate() + timedelta(days=30),
            'tax_rate': Decimal('0'),
            'notes': '',
        }
        allowance_invoice = create_invoice_from_selection(
            selection_id=selection.pk, actor=admin, form_data=form_data
        )
        overage_invoice = create_invoice_from_change_order(
            change_order_id=change.pk, actor=admin, form_data=form_data
        )
        allowance_line = allowance_invoice.line_items.get()
        allowance_line.cost_code = code
        allowance_line.save()
        self.assertEqual(overage_invoice.line_items.get().cost_code, code)
        self.assertEqual(
            project_financial_summary(project)['invoiced_total'], Decimal('0.00')
        )
        self.assertEqual(
            project_financial_summary(project)['total_project_cost'],
            Decimal('100800.00'),
        )

        connection = QuickBooksConnection.objects.create(
            organization=company,
            realm_id='pilot-sandbox',
            environment='sandbox',
            capabilities={'accounting_write': True},
            capabilities_checked_at=timezone.now(),
        )
        QuickBooksProjectCustomerMapping.objects.create(
            project=project,
            connection=connection,
            quickbooks_customer_id='42',
            quickbooks_display_name=project.name,
        )
        QuickBooksItemMapping.objects.create(
            cost_code=code,
            connection=connection,
            quickbooks_item_id='7',
            quickbooks_item_name='Tile',
        )
        api = Mock()
        for invoice in (allowance_invoice, overage_invoice):
            self.post_as(admin, 'billing:invoice_issue', (project.pk, invoice.pk))
            invoice.refresh_from_db()
            api.create_invoice.return_value = {
                'Id': str(invoice.pk),
                'SyncToken': '0',
                'DocNumber': str(invoice.number),
                'CustomerRef': {'value': '42'},
                'TotalAmt': str(invoice.total_amount),
                'Balance': str(invoice.total_amount),
            }
            attempt = start_invoice_sync(
                invoice_id=invoice.pk,
                connection_id=connection.pk,
                actor=admin,
                api_client=api,
            )
            self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
            self.assertEqual(
                api.create_invoice.call_args.args[1]['Line'][0]['SalesItemLineDetail'][
                    'ItemRef'
                ],
                {'value': '7'},
            )

        # A single receipt settles both invoices; each receives only its own share.
        payment = {
            'Id': 'receipt-1',
            'SyncToken': '0',
            'TotalAmt': '5800.00',
            'TxnDate': timezone.localdate().isoformat(),
            'CustomerRef': {'value': '42'},
            'Line': [
                {
                    'Amount': str(invoice.total_amount),
                    'LinkedTxn': [{'TxnId': str(invoice.pk), 'TxnType': 'Invoice'}],
                }
                for invoice in (allowance_invoice, overage_invoice)
            ],
        }
        api.find_payments_for_invoice.return_value = [payment]
        api.get_payment.return_value = payment
        for _ in range(2):
            for invoice in (allowance_invoice, overage_invoice):
                attempt = start_invoice_payment_sync(
                    invoice_id=invoice.pk,
                    connection_id=connection.pk,
                    actor=admin,
                    api_client=api,
                )
                self.assertEqual(attempt.status, QuickBooksSyncAttempt.Status.SUCCEEDED)
                invoice.refresh_from_db()
                self.assertEqual(invoice.status, Invoice.Status.PAID)
                self.assertEqual(invoice.balance_due, Decimal('0.00'))
        self.assertEqual(Payment.objects.count(), 2)
        summary = project_financial_summary(project)
        self.assertEqual(summary['invoiced_total'], Decimal('5800.00'))
        self.assertEqual(summary['paid_total'], Decimal('5800.00'))
        self.assertEqual(summary['balance_due'], Decimal('0.00'))
        project.refresh_from_db()
        self.assertEqual(project.contract_amount, Decimal('100000.00'))
        self.client.force_login(homeowner)
        response = self.client.get(
            reverse('projects:financial_summary', args=(project.pk,))
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Budget, cost, and margin')
        self.assertNotContains(response, '70000')
        response = self.client.get(
            reverse('billing:invoice_pdf', args=(project.pk, overage_invoice.pk))
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
