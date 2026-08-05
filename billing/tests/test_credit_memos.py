from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import CreditMemo, Invoice, InvoiceLineItem, Payment
from billing.services import (
    apply_credit_memo,
    create_credit_memo_from_change_order,
    delete_payment,
    issue_credit_memo,
    issue_invoice,
    void_credit_memo,
)
from projects.models import (
    ChangeOrder,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
)
from projects.tests import grant_internal_access


class CreditMemoTestCase(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(email='admin@example.com')
        self.staff = user_model.objects.create_user(email='staff@example.com')
        self.client_user = user_model.objects.create_user(email='client@example.com')
        self.organization = Organization.objects.create(name='Acme', slug='acme')
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.admin,
            role=OrganizationMembership.Role.ADMIN,
        )
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.staff,
            role=OrganizationMembership.Role.STAFF,
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
        grant_internal_access(self.staff, self.project, can_manage=False)

    def create_change_order(self, *, number=1, price_delta=Decimal('-500.00')):
        now = timezone.now()
        return ChangeOrder.objects.create(
            project=self.project,
            number=number,
            title='Tile allowance credit',
            description='Apply the tile allowance credit.',
            price_delta=price_delta,
            status=ChangeOrder.Status.APPROVED,
            created_by=self.admin,
            submitted_by=self.admin,
            submitted_at=now,
            decided_by=self.client_user,
            decided_at=now,
        )

    def create_credit_memo(self, *, number=1, price_delta=Decimal('-500.00')):
        change_order = self.create_change_order(number=number, price_delta=price_delta)
        return create_credit_memo_from_change_order(
            change_order_id=change_order.pk, actor=self.admin
        )

    def create_issued_invoice(self, *, number, price='300.00'):
        invoice = Invoice.objects.create(
            organization=self.organization,
            project=self.project,
            title=f'Invoice {number}',
            due_date=timezone.localdate() + timedelta(days=30),
            created_by=self.admin,
        )
        line = InvoiceLineItem(
            invoice=invoice,
            description='Construction services',
            quantity=Decimal('1.00'),
            unit_price=Decimal(price),
        )
        line.full_clean()
        line.save()
        invoice.recalculate_totals()
        return issue_invoice(invoice_id=invoice.pk, actor=self.admin)

    def test_create_credit_memo_from_approved_credit_change_order(self):
        change_order = self.create_change_order(price_delta=Decimal('-500.00'))

        credit_memo = create_credit_memo_from_change_order(
            change_order_id=change_order.pk, actor=self.admin
        )

        self.assertEqual(credit_memo.status, CreditMemo.Status.DRAFT)
        self.assertEqual(credit_memo.amount, Decimal('500.00'))
        self.assertEqual(credit_memo.source_change_order, change_order)

    def test_rejects_positive_change_order(self):
        change_order = self.create_change_order(price_delta=Decimal('500.00'))

        with self.assertRaises(ValidationError):
            create_credit_memo_from_change_order(
                change_order_id=change_order.pk, actor=self.admin
            )

    def test_rejects_non_approved_change_order(self):
        change_order = self.create_change_order(price_delta=Decimal('-500.00'))
        change_order.status = ChangeOrder.Status.PENDING
        change_order.decided_by = None
        change_order.decided_at = None
        change_order.full_clean()
        change_order.save()

        with self.assertRaises(ValidationError):
            create_credit_memo_from_change_order(
                change_order_id=change_order.pk, actor=self.admin
            )

    def test_rejects_second_credit_memo_for_same_change_order(self):
        change_order = self.create_change_order(price_delta=Decimal('-500.00'))
        create_credit_memo_from_change_order(change_order_id=change_order.pk, actor=self.admin)

        with self.assertRaises(ValidationError):
            create_credit_memo_from_change_order(
                change_order_id=change_order.pk, actor=self.admin
            )

    def test_issue_assigns_sequential_number(self):
        first = self.create_credit_memo(number=1)
        second = self.create_credit_memo(number=2)

        first = issue_credit_memo(credit_memo_id=first.pk, actor=self.admin)
        second = issue_credit_memo(credit_memo_id=second.pk, actor=self.admin)

        self.assertEqual(first.number, 1)
        self.assertEqual(second.number, 2)
        self.assertEqual(first.status, CreditMemo.Status.ISSUED)

    def test_apply_creates_credit_memo_payment_and_updates_invoice(self):
        credit_memo = self.create_credit_memo(price_delta=Decimal('-500.00'))
        credit_memo = issue_credit_memo(credit_memo_id=credit_memo.pk, actor=self.admin)
        invoice = self.create_issued_invoice(number=1, price='300.00')

        payment = apply_credit_memo(
            credit_memo_id=credit_memo.pk,
            invoice_id=invoice.pk,
            amount=Decimal('300.00'),
            actor=self.admin,
        )

        self.assertEqual(payment.method, Payment.Method.CREDIT_MEMO)
        self.assertEqual(payment.credit_memo, credit_memo)
        self.assertEqual(payment.amount, Decimal('300.00'))
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(invoice.amount_paid, Decimal('300.00'))
        credit_memo.refresh_from_db()
        self.assertEqual(credit_memo.remaining_balance, Decimal('200.00'))
        self.assertEqual(credit_memo.applied_amount, Decimal('300.00'))

    def test_apply_rejects_amount_exceeding_remaining_balance(self):
        credit_memo = self.create_credit_memo(price_delta=Decimal('-500.00'))
        credit_memo = issue_credit_memo(credit_memo_id=credit_memo.pk, actor=self.admin)
        invoice = self.create_issued_invoice(number=1, price='900.00')

        with self.assertRaises(ValidationError):
            apply_credit_memo(
                credit_memo_id=credit_memo.pk,
                invoice_id=invoice.pk,
                amount=Decimal('600.00'),
                actor=self.admin,
            )

    def test_apply_rejects_amount_exceeding_invoice_balance_due(self):
        credit_memo = self.create_credit_memo(price_delta=Decimal('-500.00'))
        credit_memo = issue_credit_memo(credit_memo_id=credit_memo.pk, actor=self.admin)
        invoice = self.create_issued_invoice(number=1, price='300.00')

        with self.assertRaises(ValidationError):
            apply_credit_memo(
                credit_memo_id=credit_memo.pk,
                invoice_id=invoice.pk,
                amount=Decimal('400.00'),
                actor=self.admin,
            )

    def test_apply_splits_across_two_invoices(self):
        credit_memo = self.create_credit_memo(price_delta=Decimal('-500.00'))
        credit_memo = issue_credit_memo(credit_memo_id=credit_memo.pk, actor=self.admin)
        first_invoice = self.create_issued_invoice(number=1, price='300.00')
        second_invoice = self.create_issued_invoice(number=2, price='200.00')

        apply_credit_memo(
            credit_memo_id=credit_memo.pk,
            invoice_id=first_invoice.pk,
            amount=Decimal('300.00'),
            actor=self.admin,
        )
        apply_credit_memo(
            credit_memo_id=credit_memo.pk,
            invoice_id=second_invoice.pk,
            amount=Decimal('200.00'),
            actor=self.admin,
        )

        credit_memo.refresh_from_db()
        self.assertEqual(credit_memo.remaining_balance, Decimal('0.00'))
        first_invoice.refresh_from_db()
        second_invoice.refresh_from_db()
        self.assertEqual(first_invoice.status, Invoice.Status.PAID)
        self.assertEqual(second_invoice.status, Invoice.Status.PAID)

    def test_void_rejected_once_anything_applied(self):
        credit_memo = self.create_credit_memo(price_delta=Decimal('-500.00'))
        credit_memo = issue_credit_memo(credit_memo_id=credit_memo.pk, actor=self.admin)
        invoice = self.create_issued_invoice(number=1, price='300.00')
        apply_credit_memo(
            credit_memo_id=credit_memo.pk,
            invoice_id=invoice.pk,
            amount=Decimal('100.00'),
            actor=self.admin,
        )

        with self.assertRaises(ValidationError):
            void_credit_memo(credit_memo_id=credit_memo.pk, actor=self.admin, reason='Mistake')

    def test_void_allowed_when_nothing_applied(self):
        credit_memo = self.create_credit_memo(price_delta=Decimal('-500.00'))
        credit_memo = issue_credit_memo(credit_memo_id=credit_memo.pk, actor=self.admin)

        credit_memo = void_credit_memo(
            credit_memo_id=credit_memo.pk, actor=self.admin, reason='No longer needed'
        )

        self.assertEqual(credit_memo.status, CreditMemo.Status.VOIDED)

    def test_deleting_applied_payment_restores_remaining_balance(self):
        credit_memo = self.create_credit_memo(price_delta=Decimal('-500.00'))
        credit_memo = issue_credit_memo(credit_memo_id=credit_memo.pk, actor=self.admin)
        invoice = self.create_issued_invoice(number=1, price='300.00')
        payment = apply_credit_memo(
            credit_memo_id=credit_memo.pk,
            invoice_id=invoice.pk,
            amount=Decimal('300.00'),
            actor=self.admin,
        )

        delete_payment(payment_id=payment.pk, actor=self.admin)

        credit_memo.refresh_from_db()
        self.assertEqual(credit_memo.remaining_balance, Decimal('500.00'))

    def test_non_admin_cannot_issue_credit_memo(self):
        credit_memo = self.create_credit_memo(price_delta=Decimal('-500.00'))
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse('billing:credit_memo_issue', args=(self.project.pk, credit_memo.pk))
        )

        self.assertEqual(response.status_code, 403)

    def test_admin_can_view_credit_memo_detail(self):
        credit_memo = self.create_credit_memo(price_delta=Decimal('-500.00'))
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse('billing:credit_memo_detail', args=(self.project.pk, credit_memo.pk))
        )

        self.assertEqual(response.status_code, 200)
