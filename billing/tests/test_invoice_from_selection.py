from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice, InvoiceLineItem
from billing.services import create_invoice_from_selection
from projects.models import (
    FinishSelection,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
    SelectionOption,
)


class InvoiceFromSelectionTestCase(TestCase):
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

    def create_selection(
        self,
        *,
        project=None,
        number=1,
        allowance=Decimal('5000.00'),
        option_price=Decimal('5800.00'),
        status=FinishSelection.Status.SELECTED,
    ):
        project = project or self.project
        selection = FinishSelection.objects.create(
            project=project,
            number=number,
            title='Tile',
            description='Choose the tile finish.',
            location='Bathroom',
            allowance_amount=allowance,
            due_date=date(2026, 8, 15),
            status=FinishSelection.Status.OPEN,
            created_by=self.admin,
            opened_by=self.admin,
            opened_at=timezone.now(),
        )
        if status == FinishSelection.Status.SELECTED:
            option = SelectionOption.objects.create(
                selection=selection,
                name='Standard tile',
                description='Standard-grade porcelain tile.',
                price=option_price,
                cost=option_price - Decimal('200.00'),
            )
            selection.status = FinishSelection.Status.SELECTED
            selection.chosen_option = option
            selection.selected_by = self.client_user
            selection.selected_at = timezone.now()
            selection.save(
                update_fields=(
                    'status', 'chosen_option', 'selected_by', 'selected_at', 'updated_at'
                )
            )
        return selection

    def invoice_form_data(self):
        return {
            'title': 'Tile allowance',
            'due_date': timezone.localdate() + timedelta(days=30),
            'tax_rate': Decimal('0'),
            'notes': '',
        }

    def test_creates_draft_invoice_billing_the_allowance_amount(self):
        selection = self.create_selection(allowance=Decimal('5000.00'), option_price=Decimal('5800.00'))

        invoice = create_invoice_from_selection(
            selection_id=selection.pk,
            actor=self.admin,
            form_data=self.invoice_form_data(),
        )

        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertEqual(invoice.source_selection, selection)
        line_items = list(invoice.line_items.all())
        self.assertEqual(len(line_items), 1)
        line = line_items[0]
        self.assertEqual(line.category, InvoiceLineItem.Category.ALLOWANCE)
        self.assertEqual(line.unit_price, Decimal('5000.00'))
        self.assertEqual(invoice.subtotal_amount, Decimal('5000.00'))

    def test_bills_allowance_not_option_price_even_with_an_overage(self):
        selection = self.create_selection(allowance=Decimal('5000.00'), option_price=Decimal('5800.00'))
        self.assertTrue(selection.requires_change_order)

        invoice = create_invoice_from_selection(
            selection_id=selection.pk,
            actor=self.admin,
            form_data=self.invoice_form_data(),
        )

        self.assertEqual(invoice.subtotal_amount, Decimal('5000.00'))

    def test_works_independent_of_credit(self):
        selection = self.create_selection(allowance=Decimal('5000.00'), option_price=Decimal('4500.00'))
        self.assertTrue(selection.has_credit)

        invoice = create_invoice_from_selection(
            selection_id=selection.pk,
            actor=self.admin,
            form_data=self.invoice_form_data(),
        )

        self.assertEqual(invoice.subtotal_amount, Decimal('5000.00'))

    def test_rejects_selection_not_yet_selected(self):
        selection = self.create_selection(status=FinishSelection.Status.OPEN)

        with self.assertRaises(ValidationError):
            create_invoice_from_selection(
                selection_id=selection.pk,
                actor=self.admin,
                form_data=self.invoice_form_data(),
            )

    def test_rejects_second_invoice_for_same_selection(self):
        selection = self.create_selection()
        create_invoice_from_selection(
            selection_id=selection.pk,
            actor=self.admin,
            form_data=self.invoice_form_data(),
        )

        with self.assertRaises(ValidationError):
            create_invoice_from_selection(
                selection_id=selection.pk,
                actor=self.admin,
                form_data=self.invoice_form_data(),
            )

    def test_selection_detail_links_to_invoice_once_created(self):
        selection = self.create_selection()
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse('projects:selection_detail', args=(self.project.pk, selection.pk))
        )
        self.assertContains(response, 'Create invoice for allowance')

        create_invoice_from_selection(
            selection_id=selection.pk,
            actor=self.admin,
            form_data=self.invoice_form_data(),
        )
        response = self.client.get(
            reverse('projects:selection_detail', args=(self.project.pk, selection.pk))
        )
        self.assertContains(response, 'Allowance invoiced')
