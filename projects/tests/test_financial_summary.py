from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice, InvoiceLineItem
from billing.services import issue_invoice, record_payment
from projects.financials import project_financial_summary
from projects.models import (
    ChangeOrder,
    Estimate,
    FinishSelection,
    Organization,
    OrganizationMembership,
    Project,
    ProjectCostEntry,
    ProjectMembership,
    SelectionOption,
)
from projects.tests import grant_internal_access


class ProjectFinancialSummaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(
            name='Example Builders', slug='example-builders-fin'
        )
        cls.project = Project.objects.create(
            organization=cls.organization,
            name='Oak Street',
            status=Project.Status.ACTIVE,
            contract_amount=Decimal('100000.00'),
        )
        cls.admin_user = get_user_model().objects.create_user(
            'admin-fin@example.com', 'password'
        )
        cls.client_user = get_user_model().objects.create_user(
            'client-fin@example.com', 'password'
        )
        cls.office_manager = get_user_model().objects.create_user(
            'office-manager-fin@example.com', 'password'
        )
        OrganizationMembership.objects.create(
            organization=cls.organization,
            user=cls.admin_user,
            role=OrganizationMembership.Role.ADMIN,
        )
        OrganizationMembership.objects.create(
            organization=cls.organization,
            user=cls.client_user,
            role=OrganizationMembership.Role.CLIENT,
        )
        OrganizationMembership.objects.create(
            organization=cls.organization,
            user=cls.office_manager,
            role=OrganizationMembership.Role.OFFICE_MANAGER,
        )
        ProjectMembership.objects.create(
            project=cls.project,
            user=cls.client_user,
            role=OrganizationMembership.Role.CLIENT,
        )
        grant_internal_access(cls.admin_user, cls.project)
        grant_internal_access(cls.office_manager, cls.project, can_manage=False)

        now = timezone.now()

        Estimate.objects.create(
            project=cls.project,
            number=1,
            title='Base contract',
            subtotal_total=Decimal('100000.00'),
            price_total=Decimal('100000.00'),
            cost_total=Decimal('70000.00'),
            status=Estimate.Status.APPROVED,
            created_by=cls.admin_user,
            submitted_by=cls.admin_user,
            submitted_at=now,
            decided_by=cls.client_user,
            decided_at=now,
        )
        ProjectCostEntry.objects.create(
            project=cls.project,
            category=ProjectCostEntry.Category.MATERIAL,
            description='Lumber invoice',
            amount=Decimal('50000.00'),
            incurred_date=timezone.localdate(),
            recorded_by=cls.admin_user,
        )

        ChangeOrder.objects.create(
            project=cls.project,
            number=1,
            title='Approved scope change',
            description='Adds a covered porch.',
            price_delta=Decimal('5000.00'),
            cost_delta=Decimal('2000.00'),
            status=ChangeOrder.Status.APPROVED,
            created_by=cls.admin_user,
            submitted_by=cls.admin_user,
            submitted_at=now,
            decided_by=cls.client_user,
            decided_at=now,
        )
        ChangeOrder.objects.create(
            project=cls.project,
            number=2,
            title='Pending scope change',
            description='Adds a fence.',
            price_delta=Decimal('1000.00'),
            cost_delta=Decimal('400.00'),
            status=ChangeOrder.Status.PENDING,
            created_by=cls.admin_user,
            submitted_by=cls.admin_user,
            submitted_at=now,
        )

        overage_selection = FinishSelection.objects.create(
            project=cls.project,
            number=1,
            title='Kitchen counters',
            allowance_amount=Decimal('1000.00'),
            status=FinishSelection.Status.DRAFT,
            created_by=cls.admin_user,
        )
        overage_option = SelectionOption.objects.create(
            selection=overage_selection,
            name='Quartz',
            price=Decimal('1200.00'),
            cost=Decimal('700.00'),
        )
        overage_selection.status = FinishSelection.Status.SELECTED
        overage_selection.opened_by = cls.admin_user
        overage_selection.opened_at = now
        overage_selection.chosen_option = overage_option
        overage_selection.selected_by = cls.client_user
        overage_selection.selected_at = now
        overage_selection.save(
            update_fields=(
                'status',
                'opened_by',
                'opened_at',
                'chosen_option',
                'selected_by',
                'selected_at',
            )
        )

        credit_selection = FinishSelection.objects.create(
            project=cls.project,
            number=2,
            title='Bathroom fixtures',
            allowance_amount=Decimal('1000.00'),
            status=FinishSelection.Status.DRAFT,
            created_by=cls.admin_user,
        )
        credit_option = SelectionOption.objects.create(
            selection=credit_selection,
            name='Standard fixtures',
            price=Decimal('800.00'),
            cost=Decimal('500.00'),
        )
        credit_selection.status = FinishSelection.Status.SELECTED
        credit_selection.opened_by = cls.admin_user
        credit_selection.opened_at = now
        credit_selection.chosen_option = credit_option
        credit_selection.selected_by = cls.client_user
        credit_selection.selected_at = now
        credit_selection.credit_disposition = FinishSelection.CreditDisposition.RETAIN_AS_MARGIN
        credit_selection.credit_disposition_set_by = cls.admin_user
        credit_selection.credit_disposition_set_at = now
        credit_selection.save(
            update_fields=(
                'status',
                'opened_by',
                'opened_at',
                'chosen_option',
                'selected_by',
                'selected_at',
                'credit_disposition',
                'credit_disposition_set_by',
                'credit_disposition_set_at',
            )
        )

        invoice = Invoice.objects.create(
            organization=cls.organization,
            project=cls.project,
            title='Progress invoice',
            due_date=timezone.localdate() + timedelta(days=30),
            created_by=cls.admin_user,
        )
        InvoiceLineItem.objects.create(
            invoice=invoice,
            description='Construction services',
            quantity=Decimal('1.00'),
            unit_price=Decimal('3000.00'),
        )
        invoice.recalculate_totals()
        cls.invoice = issue_invoice(invoice_id=invoice.pk, actor=cls.admin_user)
        record_payment(
            invoice_id=cls.invoice.pk,
            actor=cls.admin_user,
            amount=Decimal('1000.00'),
            method='check',
            reference='',
            paid_date=timezone.localdate(),
            note='',
        )

    def test_summary_computes_expected_totals(self):
        summary = project_financial_summary(self.project, include_costs=True)
        self.assertEqual(summary['contract_amount'], Decimal('100000.00'))
        self.assertEqual(summary['approved_change_order_total'], Decimal('5000.00'))
        self.assertEqual(summary['pending_change_order_total'], Decimal('1000.00'))
        self.assertEqual(summary['total_project_cost'], Decimal('105000.00'))
        self.assertEqual(summary['selection_overage_total'], Decimal('200.00'))
        self.assertEqual(summary['selection_credit_total'], Decimal('200.00'))
        self.assertEqual(
            summary['selection_credit_by_disposition'].get(
                FinishSelection.CreditDisposition.RETAIN_AS_MARGIN
            ),
            Decimal('200.00'),
        )
        self.assertEqual(summary['invoiced_total'], Decimal('3000.00'))
        self.assertEqual(summary['paid_total'], Decimal('1000.00'))
        self.assertEqual(summary['balance_due'], Decimal('2000.00'))
        self.assertEqual(summary['approved_change_order_cost_total'], Decimal('2000.00'))
        self.assertEqual(summary['estimate_cost_total'], Decimal('70000.00'))
        self.assertEqual(summary['budget_amount'], Decimal('70000.00'))
        self.assertEqual(summary['committed_cost_total'], Decimal('72000.00'))
        self.assertEqual(summary['actual_cost_total'], Decimal('50000.00'))
        self.assertEqual(summary['estimated_final_cost'], Decimal('72000.00'))
        self.assertEqual(summary['estimated_margin'], Decimal('33000.00'))
        self.assertEqual(summary['profitability'], Decimal('33000.00'))

    def test_summary_excludes_costs_by_default(self):
        summary = project_financial_summary(self.project)
        self.assertNotIn('approved_change_order_cost_total', summary)
        self.assertNotIn('estimated_margin', summary)
        self.assertNotIn('profitability', summary)

    def test_management_sees_budget_and_margin_on_financial_summary_page(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse('projects:financial_summary', args=(self.project.pk,))
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Budget, cost, and margin')
        self.assertContains(response, 'Actual cost entries')

    def test_client_does_not_see_budget_and_margin_on_financial_summary_page(self):
        self.client.force_login(self.client_user)
        response = self.client.get(
            reverse('projects:financial_summary', args=(self.project.pk,))
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Budget, cost, and margin')
        self.assertContains(response, 'Contract amount')

    def test_office_manager_sees_base_rollup_but_not_budget_section(self):
        """Internal roles outside management/accounting see the base rollup, not budget data."""
        self.client.force_login(self.office_manager)
        response = self.client.get(
            reverse('projects:financial_summary', args=(self.project.pk,))
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Contract amount')
        self.assertNotContains(response, 'Budget, cost, and margin')
        self.assertNotContains(response, 'Actual cost entries')

    def test_manager_can_record_and_remove_cost_entry(self):
        self.client.force_login(self.admin_user)
        create_response = self.client.post(
            reverse('projects:cost_entry_create', args=(self.project.pk,)),
            {
                'category': 'labor',
                'description': 'Framing crew',
                'amount': '2500.00',
                'incurred_date': timezone.localdate().isoformat(),
                'note': '',
            },
        )
        self.assertEqual(create_response.status_code, 302)
        entry = ProjectCostEntry.objects.get(description='Framing crew')
        self.assertEqual(entry.amount, Decimal('2500.00'))

        delete_response = self.client.post(
            reverse('projects:cost_entry_delete', args=(self.project.pk, entry.pk))
        )
        self.assertEqual(delete_response.status_code, 302)
        self.assertFalse(ProjectCostEntry.objects.filter(pk=entry.pk).exists())

    def test_office_manager_cannot_record_cost_entry(self):
        self.client.force_login(self.office_manager)
        response = self.client.post(
            reverse('projects:cost_entry_create', args=(self.project.pk,)),
            {
                'category': 'labor',
                'description': 'Framing crew',
                'amount': '2500.00',
                'incurred_date': timezone.localdate().isoformat(),
                'note': '',
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_margin_and_profitability_exclude_tax(self):
        """Sales tax is collected on the client's behalf and must not inflate margin."""
        tax_project = Project.objects.create(
            organization=self.organization,
            name='Elm Street',
            status=Project.Status.ACTIVE,
            contract_amount=Decimal('11000.00'),  # $10,000 subtotal + 10% tax
        )
        now = timezone.now()
        Estimate.objects.create(
            project=tax_project,
            number=1,
            title='Base contract',
            subtotal_total=Decimal('10000.00'),
            tax_rate=Decimal('10.000'),
            tax_amount=Decimal('1000.00'),
            price_total=Decimal('11000.00'),
            cost_total=Decimal('6000.00'),
            status=Estimate.Status.APPROVED,
            created_by=self.admin_user,
            submitted_by=self.admin_user,
            submitted_at=now,
            decided_by=self.client_user,
            decided_at=now,
        )
        summary = project_financial_summary(tax_project, include_costs=True)
        # Pre-tax revenue (10,000) minus cost (6,000) = 4,000, not 5,000 as it
        # would be if the tax-inclusive contract amount (11,000) leaked in.
        self.assertEqual(summary['estimated_margin'], Decimal('4000.00'))
        self.assertEqual(summary['profitability'], Decimal('4000.00'))
        # The headline "total project cost" still reflects the real, tax-inclusive
        # amount the client owes.
        self.assertEqual(summary['total_project_cost'], Decimal('11000.00'))
