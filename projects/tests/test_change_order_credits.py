from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from projects.financials import project_financial_summary
from projects.models import (
    ChangeOrder,
    ChangeOrderLineItem,
    FinishSelection,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
    SelectionOption,
)
from projects.tests import grant_internal_access


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ChangeOrderCreditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(
            name='Example Builders', slug='example-builders'
        )
        cls.project = Project.objects.create(
            organization=cls.organization,
            name='Oak Street',
            status=Project.Status.ACTIVE,
        )
        cls.other_project = Project.objects.create(
            organization=cls.organization, name='Pine Street'
        )
        cls.admin_user = get_user_model().objects.create_user(
            'admin@example.com', 'password'
        )
        cls.client_user = get_user_model().objects.create_user(
            'client@example.com', 'password'
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
        ProjectMembership.objects.create(
            project=cls.project,
            user=cls.client_user,
            role=OrganizationMembership.Role.CLIENT,
        )
        grant_internal_access(cls.admin_user, cls.project, cls.other_project)

    def create_credit_selection(
        self,
        *,
        project=None,
        number=1,
        allowance=Decimal('5000.00'),
        price=Decimal('4500.00'),
        disposition=FinishSelection.CreditDisposition.APPLY_ELSEWHERE,
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
            created_by=self.admin_user,
            opened_by=self.admin_user,
            opened_at=timezone.now(),
        )
        option = SelectionOption.objects.create(
            selection=selection,
            name='Standard tile',
            description='Standard-grade porcelain tile.',
            price=price,
            cost=price - Decimal('200.00'),
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
        if disposition is not None and disposition != FinishSelection.CreditDisposition.UNDETERMINED:
            selection.credit_disposition = disposition
            selection.credit_disposition_set_by = self.admin_user
            selection.credit_disposition_set_at = timezone.now()
            selection.save(
                update_fields=(
                    'credit_disposition',
                    'credit_disposition_set_by',
                    'credit_disposition_set_at',
                    'updated_at',
                )
            )
        return selection

    def create_change_order(
        self,
        *,
        project=None,
        number=1,
        price_delta=Decimal('0.00'),
        source_selection=None,
        status=ChangeOrder.Status.DRAFT,
    ):
        project = project or self.project
        data = {
            'project': project,
            'number': number,
            'title': 'Tile allowance credit',
            'description': 'Apply the tile allowance credit to another selection.',
            'reason': 'Tile came in under allowance.',
            'price_delta': price_delta,
            'source_selection': source_selection,
            'status': status,
            'created_by': self.admin_user,
        }
        if status != ChangeOrder.Status.DRAFT:
            data.update({'submitted_by': self.admin_user, 'submitted_at': timezone.now()})
        change_order = ChangeOrder(**data)
        change_order.full_clean()
        change_order.save()
        return change_order

    def test_valid_credit_change_order_saves(self):
        selection = self.create_credit_selection()

        change_order = self.create_change_order(
            price_delta=Decimal('-500.00'), source_selection=selection
        )

        self.assertEqual(change_order.source_selection, selection)
        self.assertEqual(change_order.price_delta, Decimal('-500.00'))

    def test_rejects_positive_price_delta(self):
        selection = self.create_credit_selection()

        with self.assertRaises(ValidationError) as ctx:
            self.create_change_order(
                price_delta=Decimal('500.00'), source_selection=selection
            )
        self.assertIn('price_delta', ctx.exception.message_dict)

    def test_rejects_amount_exceeding_available_credit(self):
        selection = self.create_credit_selection()

        with self.assertRaises(ValidationError) as ctx:
            self.create_change_order(
                price_delta=Decimal('-600.00'), source_selection=selection
            )
        self.assertIn('price_delta', ctx.exception.message_dict)

    def test_rejects_undetermined_disposition(self):
        selection = self.create_credit_selection(
            disposition=FinishSelection.CreditDisposition.UNDETERMINED
        )

        with self.assertRaises(ValidationError) as ctx:
            self.create_change_order(
                price_delta=Decimal('-500.00'), source_selection=selection
            )
        self.assertIn('source_selection', ctx.exception.message_dict)

    def test_rejects_selection_from_a_different_project(self):
        selection = self.create_credit_selection(project=self.other_project)

        with self.assertRaises(ValidationError) as ctx:
            self.create_change_order(
                price_delta=Decimal('-500.00'), source_selection=selection
            )
        self.assertIn('source_selection', ctx.exception.message_dict)

    def test_rejects_second_active_credit_change_order_for_same_selection(self):
        selection = self.create_credit_selection()
        self.create_change_order(
            number=1, price_delta=Decimal('-500.00'), source_selection=selection
        )

        with self.assertRaises(ValidationError) as ctx:
            self.create_change_order(
                number=2, price_delta=Decimal('-200.00'), source_selection=selection
            )
        self.assertIn('source_selection', ctx.exception.message_dict)

    def test_allows_new_credit_change_order_after_first_is_voided(self):
        selection = self.create_credit_selection()
        first = self.create_change_order(
            number=1,
            price_delta=Decimal('-500.00'),
            source_selection=selection,
            status=ChangeOrder.Status.PENDING,
        )
        first.status = ChangeOrder.Status.VOIDED
        first.voided_by = self.admin_user
        first.voided_at = timezone.now()
        first.full_clean()
        first.save()

        second = self.create_change_order(
            number=2, price_delta=Decimal('-500.00'), source_selection=selection
        )

        self.assertEqual(second.source_selection, selection)

    def test_rejects_credit_change_order_with_line_items(self):
        selection = self.create_credit_selection()
        change_order = self.create_change_order(number=1, price_delta=Decimal('0.00'))
        ChangeOrderLineItem.objects.create(
            change_order=change_order,
            description='Framing labor',
            quantity=Decimal('1.00'),
            unit_price=Decimal('100.00'),
            unit_cost=Decimal('50.00'),
        )
        change_order.recalculate_from_line_items()
        change_order.refresh_from_db()

        change_order.source_selection = selection
        change_order.price_delta = Decimal('-500.00')
        with self.assertRaises(ValidationError) as ctx:
            change_order.full_clean()
        self.assertIn('source_selection', ctx.exception.message_dict)

    def test_financial_summary_excludes_credit_once_a_change_order_exists(self):
        selection = self.create_credit_selection()

        summary = project_financial_summary(self.project)
        self.assertEqual(summary['selection_credit_total'], Decimal('500.00'))

        change_order = self.create_change_order(
            price_delta=Decimal('-500.00'),
            source_selection=selection,
            status=ChangeOrder.Status.PENDING,
        )
        summary = project_financial_summary(self.project)
        self.assertEqual(summary['selection_credit_total'], Decimal('0.00'))

        change_order.status = ChangeOrder.Status.VOIDED
        change_order.voided_by = self.admin_user
        change_order.voided_at = timezone.now()
        change_order.full_clean()
        change_order.save()
        summary = project_financial_summary(self.project)
        self.assertEqual(summary['selection_credit_total'], Decimal('500.00'))
