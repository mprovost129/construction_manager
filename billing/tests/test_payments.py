from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice, Payment
from billing.services import delete_payment, record_payment
from billing.tests.test_invoices import InvoiceTestCase


class PaymentServiceTests(InvoiceTestCase):
    def test_record_payment_marks_invoice_partially_paid(self):
        invoice = self.create_issued(price='500.00')
        payment = record_payment(
            invoice_id=invoice.pk,
            actor=self.admin,
            amount=Decimal('200.00'),
            method='check',
            reference='1001',
            paid_date=timezone.localdate(),
            note='',
        )
        invoice.refresh_from_db()
        self.assertEqual(payment.amount, Decimal('200.00'))
        self.assertEqual(invoice.amount_paid, Decimal('200.00'))
        self.assertEqual(invoice.status, Invoice.Status.PARTIALLY_PAID)
        self.assertEqual(invoice.balance_due, Decimal('300.00'))

    def test_record_payment_in_full_marks_invoice_paid(self):
        invoice = self.create_issued(price='500.00')
        record_payment(
            invoice_id=invoice.pk,
            actor=self.admin,
            amount=Decimal('500.00'),
            method='ach',
            reference='',
            paid_date=timezone.localdate(),
            note='',
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(invoice.balance_due, Decimal('0'))

    def test_payment_cannot_exceed_balance_due(self):
        invoice = self.create_issued(price='500.00')
        with self.assertRaises(ValidationError):
            record_payment(
                invoice_id=invoice.pk,
                actor=self.admin,
                amount=Decimal('600.00'),
                method='cash',
                reference='',
                paid_date=timezone.localdate(),
                note='',
            )

    def test_payment_rejected_on_draft_invoice(self):
        invoice = self.create_draft()
        self.add_line(invoice)
        with self.assertRaises(ValidationError):
            record_payment(
                invoice_id=invoice.pk,
                actor=self.admin,
                amount=Decimal('10.00'),
                method='cash',
                reference='',
                paid_date=timezone.localdate(),
                note='',
            )

    def test_delete_payment_recomputes_invoice_state(self):
        invoice = self.create_issued(price='500.00')
        payment = record_payment(
            invoice_id=invoice.pk,
            actor=self.admin,
            amount=Decimal('500.00'),
            method='cash',
            reference='',
            paid_date=timezone.localdate(),
            note='',
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)

        delete_payment(payment_id=payment.pk, actor=self.admin)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)
        self.assertEqual(invoice.amount_paid, Decimal('0'))
        self.assertFalse(Payment.objects.filter(pk=payment.pk).exists())


class PaymentViewTests(InvoiceTestCase):
    def test_manager_can_record_payment_through_view(self):
        invoice = self.create_issued(price='500.00')
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('billing:payment_create', args=(self.project.pk, invoice.pk)),
            {
                'amount': '200.00',
                'method': 'check',
                'reference': '1001',
                'paid_date': timezone.localdate().isoformat(),
                'note': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount_paid, Decimal('200.00'))

    def test_client_cannot_record_payment(self):
        invoice = self.create_issued(price='500.00')
        self.client.force_login(self.client_user)
        response = self.client.post(
            reverse('billing:payment_create', args=(self.project.pk, invoice.pk)),
            {
                'amount': '200.00',
                'method': 'check',
                'reference': '',
                'paid_date': timezone.localdate().isoformat(),
                'note': '',
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_client_sees_payment_history_on_invoice_detail(self):
        invoice = self.create_issued(price='500.00')
        record_payment(
            invoice_id=invoice.pk,
            actor=self.admin,
            amount=Decimal('200.00'),
            method='check',
            reference='1001',
            paid_date=timezone.localdate(),
            note='',
        )
        self.client.force_login(self.client_user)
        response = self.client.get(
            reverse('billing:invoice_detail', args=(self.project.pk, invoice.pk))
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '200.00')
