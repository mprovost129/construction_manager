from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice, InvoiceLineItem
from billing.services import (
    create_invoice_from_change_order,
    issue_invoice,
    void_invoice,
)
from projects.models import (
    ActivityEvent,
    ChangeOrder,
    ChangeOrderLineItem,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
)


class InvoiceTestCase(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(email='admin@example.com')
        self.client_user = user_model.objects.create_user(email='client@example.com')
        self.outsider = user_model.objects.create_user(email='outsider@example.com')
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

    def create_draft(self, *, title='Progress invoice', tax_rate='0.000'):
        return Invoice.objects.create(
            organization=self.organization,
            project=self.project,
            title=title,
            due_date=timezone.localdate() + timedelta(days=30),
            tax_rate=Decimal(tax_rate),
            created_by=self.admin,
        )

    def add_line(self, invoice, *, description='Construction services', price='100.00'):
        line = InvoiceLineItem(
            invoice=invoice,
            description=description,
            quantity=Decimal('1.00'),
            unit_price=Decimal(price),
        )
        line.full_clean()
        line.save()
        invoice.recalculate_totals()
        return line

    def create_issued(self, *, title='Progress invoice', price='100.00'):
        invoice = self.create_draft(title=title)
        self.add_line(invoice, price=price)
        return issue_invoice(invoice_id=invoice.pk, actor=self.admin)


class InvoiceModelAndServiceTests(InvoiceTestCase):
    def test_line_items_recalculate_subtotal_tax_total_and_balance(self):
        invoice = self.create_draft(tax_rate='10.000')
        self.add_line(invoice, price='10.50')
        self.add_line(invoice, description='Materials', price='20.00')
        invoice.refresh_from_db()

        self.assertEqual(invoice.subtotal_amount, Decimal('30.50'))
        self.assertEqual(invoice.tax_amount, Decimal('3.05'))
        self.assertEqual(invoice.total_amount, Decimal('33.55'))
        self.assertEqual(invoice.balance_due, Decimal('33.55'))

    def test_issue_assigns_company_wide_sequential_immutable_numbers(self):
        first = self.create_issued(title='First')
        other_project = Project.objects.create(
            organization=self.organization,
            name='Jones Residence',
            created_by=self.admin,
        )
        ProjectMembership.objects.create(
            project=other_project,
            user=self.client_user,
            role=OrganizationMembership.Role.CLIENT,
        )
        second = Invoice.objects.create(
            organization=self.organization,
            project=other_project,
            title='Second',
            due_date=timezone.localdate() + timedelta(days=30),
            created_by=self.admin,
        )
        self.add_line(second)
        second = issue_invoice(invoice_id=second.pk, actor=self.admin)

        self.assertEqual(first.number, 1)
        self.assertEqual(second.number, 2)
        first.number = 99
        with self.assertRaisesMessage(ValidationError, 'immutable'):
            first.save()

    def test_issue_requires_line_due_date_and_active_client(self):
        invoice = self.create_draft()
        with self.assertRaisesMessage(ValidationError, 'line item'):
            issue_invoice(invoice_id=invoice.pk, actor=self.admin)

        self.add_line(invoice)
        self.project.project_memberships.update(is_active=False)
        with self.assertRaisesMessage(ValidationError, 'active client'):
            issue_invoice(invoice_id=invoice.pk, actor=self.admin)

    def test_issued_invoice_and_lines_are_immutable(self):
        invoice = self.create_issued()
        invoice.title = 'Changed title'
        with self.assertRaisesMessage(ValidationError, 'immutable'):
            invoice.save()

        line = invoice.line_items.first()
        line.description = 'Changed line'
        with self.assertRaisesMessage(ValidationError, 'immutable'):
            line.save()
        with self.assertRaisesMessage(ValidationError, 'immutable'):
            line.delete()

    def test_unpaid_invoice_can_be_voided_without_reusing_number(self):
        invoice = self.create_issued()
        number = invoice.number

        invoice = void_invoice(
            invoice_id=invoice.pk,
            actor=self.admin,
            reason='Issued in error',
        )

        self.assertEqual(invoice.status, Invoice.Status.VOIDED)
        self.assertEqual(invoice.number, number)
        self.assertEqual(invoice.void_reason, 'Issued in error')

    def test_paid_or_partially_paid_invoice_cannot_be_voided_locally(self):
        invoice = self.create_issued()
        Invoice.objects.filter(pk=invoice.pk).update(
            status=Invoice.Status.PARTIALLY_PAID,
            amount_paid=Decimal('25.00'),
        )

        with self.assertRaisesMessage(ValidationError, 'unpaid issued'):
            void_invoice(
                invoice_id=invoice.pk,
                actor=self.admin,
                reason='Wrong invoice',
            )

    def test_approved_change_order_creates_itemized_invoice_draft(self):
        now = timezone.now()
        change_order = ChangeOrder.objects.create(
            project=self.project,
            number=1,
            title='Add built-ins',
            description='Install built-ins',
            price_delta=Decimal('300.00'),
            status=ChangeOrder.Status.APPROVED,
            created_by=self.admin,
            submitted_by=self.admin,
            submitted_at=now,
            decided_by=self.client_user,
            decided_at=now,
        )
        ChangeOrderLineItem.objects.create(
            change_order=change_order,
            description='Cabinet materials',
            quantity=Decimal('2.00'),
            unit_price=Decimal('150.00'),
        )

        invoice = create_invoice_from_change_order(
            change_order_id=change_order.pk,
            actor=self.admin,
            form_data={
                'title': 'Built-ins invoice',
                'due_date': timezone.localdate() + timedelta(days=30),
                'tax_rate': Decimal('0'),
                'notes': '',
            },
        )

        self.assertEqual(invoice.source_change_order, change_order)
        self.assertEqual(invoice.total_amount, Decimal('300.00'))
        self.assertEqual(invoice.line_items.count(), 1)
        with self.assertRaisesMessage(ValidationError, 'already has an invoice'):
            create_invoice_from_change_order(
                change_order_id=change_order.pk,
                actor=self.admin,
                form_data={
                    'title': 'Duplicate',
                    'due_date': timezone.localdate() + timedelta(days=30),
                    'tax_rate': Decimal('0'),
                    'notes': '',
                },
            )

    def test_nonpositive_change_order_requires_credit_memo_workflow(self):
        now = timezone.now()
        change_order = ChangeOrder.objects.create(
            project=self.project,
            number=1,
            title='Credit',
            description='Client credit',
            price_delta=Decimal('-25.00'),
            status=ChangeOrder.Status.APPROVED,
            created_by=self.admin,
            submitted_by=self.admin,
            submitted_at=now,
            decided_by=self.client_user,
            decided_at=now,
        )

        with self.assertRaisesMessage(ValidationError, 'credit-memo'):
            create_invoice_from_change_order(
                change_order_id=change_order.pk,
                actor=self.admin,
                form_data={
                    'title': 'Credit',
                    'due_date': timezone.localdate() + timedelta(days=30),
                    'tax_rate': Decimal('0'),
                    'notes': '',
                },
            )


class InvoiceTaxRateTests(InvoiceTestCase):
    def test_new_draft_defaults_tax_rate_from_organization(self):
        self.organization.default_tax_rate = Decimal('6.500')
        self.organization.save(update_fields=('default_tax_rate',))
        self.client.force_login(self.admin)
        response = self.client.get(reverse('billing:invoice_create', args=(self.project.pk,)))
        self.assertContains(response, '6.5')

    def test_creating_invoice_without_explicit_rate_uses_organization_default(self):
        self.organization.default_tax_rate = Decimal('8.000')
        self.organization.save(update_fields=('default_tax_rate',))
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('billing:invoice_create', args=(self.project.pk,)),
            {
                'title': 'Deposit invoice',
                'due_date': timezone.localdate() + timedelta(days=14),
                'notes': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get(title='Deposit invoice')
        self.assertEqual(invoice.tax_rate, Decimal('8.000'))

    def test_editing_draft_tax_rate_recalculates_tax_and_total(self):
        invoice = self.create_draft()
        self.add_line(invoice, price='100.00')
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('billing:invoice_edit', args=(self.project.pk, invoice.pk)),
            {
                'title': invoice.title,
                'due_date': invoice.due_date.isoformat(),
                'tax_rate': '20.000',
                'notes': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.tax_rate, Decimal('20.000'))
        self.assertEqual(invoice.tax_amount, Decimal('20.00'))
        self.assertEqual(invoice.total_amount, Decimal('120.00'))

    def test_tax_rate_immutable_once_issued(self):
        invoice = self.create_issued(price='100.00')
        invoice.tax_rate = Decimal('99.000')
        with self.assertRaisesMessage(ValidationError, 'immutable'):
            invoice.save()


class InvoicePortalTests(InvoiceTestCase):
    def test_admin_can_create_itemize_and_issue_invoice(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('billing:invoice_create', args=(self.project.pk,)),
            {
                'title': 'Deposit invoice',
                'due_date': timezone.localdate() + timedelta(days=14),
                'tax_rate': '5.00',
                'notes': 'Thank you.',
            },
        )
        invoice = Invoice.objects.get(title='Deposit invoice')
        self.assertRedirects(
            response,
            reverse('billing:invoice_detail', args=(self.project.pk, invoice.pk)),
        )

        self.client.post(
            reverse(
                'billing:invoice_line_create',
                args=(self.project.pk, invoice.pk),
            ),
            {
                'category': InvoiceLineItem.Category.LABOR,
                'description': 'Labor deposit',
                'quantity': '1',
                'unit_price': '100.00',
                'sort_order': '0',
            },
        )
        response = self.client.post(
            reverse('billing:invoice_issue', args=(self.project.pk, invoice.pk)),
            follow=True,
        )

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)
        self.assertEqual(invoice.total_amount, Decimal('105.00'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertContains(response, 'INV-000001', status_code=200)

    def test_client_cannot_see_draft_but_can_see_issued_invoice(self):
        draft = self.create_draft()
        self.add_line(draft)
        self.client.force_login(self.client_user)
        detail_url = reverse(
            'billing:invoice_detail',
            args=(self.project.pk, draft.pk),
        )

        self.assertEqual(self.client.get(detail_url).status_code, 404)
        issue_invoice(invoice_id=draft.pk, actor=self.admin)
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Balance due')
        self.assertContains(response, 'Online payment is not available')

    def test_client_can_download_authenticated_issued_invoice_pdf(self):
        invoice = self.create_issued(title='Kitchen progress invoice')
        self.client.force_login(self.client_user)

        response = self.client.get(
            reverse('billing:invoice_pdf', args=(self.project.pk, invoice.pk))
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertEqual(
            response['Content-Disposition'],
            'attachment; filename="inv-000001-invoice.pdf"',
        )
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertGreater(len(response.content), 2000)

    def test_client_cannot_download_draft_invoice_pdf(self):
        invoice = self.create_draft()
        self.add_line(invoice)
        self.client.force_login(self.client_user)

        response = self.client.get(
            reverse('billing:invoice_pdf', args=(self.project.pk, invoice.pk))
        )

        self.assertEqual(response.status_code, 404)

    def test_invoice_detail_links_to_pdf_download(self):
        invoice = self.create_issued()
        self.client.force_login(self.client_user)
        pdf_url = reverse(
            'billing:invoice_pdf',
            args=(self.project.pk, invoice.pk),
        )

        response = self.client.get(
            reverse('billing:invoice_detail', args=(self.project.pk, invoice.pk))
        )

        self.assertContains(response, pdf_url)
        self.assertContains(response, 'Download PDF')

    def test_client_cannot_create_or_modify_invoices(self):
        invoice = self.create_draft()
        self.client.force_login(self.client_user)

        self.assertEqual(
            self.client.get(
                reverse('billing:invoice_create', args=(self.project.pk,))
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse('billing:invoice_issue', args=(self.project.pk, invoice.pk))
            ).status_code,
            403,
        )

    def test_outsider_cannot_access_project_invoice_routes(self):
        self.client.force_login(self.outsider)
        response = self.client.get(
            reverse('billing:invoice_list', args=(self.project.pk,))
        )
        self.assertEqual(response.status_code, 404)

    def test_invoice_question_prefills_project_conversation_subject(self):
        invoice = self.create_issued()
        self.client.force_login(self.client_user)
        response = self.client.get(
            reverse('billing:invoice_detail', args=(self.project.pk, invoice.pk))
        )
        self.assertContains(response, 'subject=Question+about+INV-000001')

        response = self.client.get(
            reverse('projects:message_create', args=(self.project.pk,)),
            {'subject': 'Question about INV-000001'},
        )
        self.assertContains(response, 'value="Question about INV-000001"')

    def test_project_page_links_authorized_users_to_invoices(self):
        self.client.force_login(self.client_user)
        response = self.client.get(
            reverse('projects:detail', args=(self.project.pk,))
        )
        self.assertContains(response, reverse('billing:invoice_list', args=(self.project.pk,)))

    def test_issued_invoice_can_be_voided_from_admin_workflow(self):
        invoice = self.create_issued()
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('billing:invoice_void', args=(self.project.pk, invoice.pk)),
            {'reason': 'Duplicate billing'},
        )

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.VOIDED)
        self.assertRedirects(
            response,
            reverse('billing:invoice_detail', args=(self.project.pk, invoice.pk)),
        )

    def test_admin_can_discard_draft_and_preserve_activity(self):
        invoice = self.create_draft(title='Temporary draft')
        invoice_id = invoice.pk
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('billing:invoice_discard', args=(self.project.pk, invoice.pk)),
        )

        self.assertFalse(Invoice.objects.filter(pk=invoice_id).exists())
        self.assertTrue(
            ActivityEvent.objects.filter(
                project=self.project,
                event_type=ActivityEvent.Type.INVOICE_DRAFT_DISCARDED,
                metadata__invoice_id=invoice_id,
            ).exists()
        )
        self.assertRedirects(
            response,
            reverse('billing:invoice_list', args=(self.project.pk,)),
        )

    def test_change_order_cannot_be_voided_until_related_draft_is_discarded(self):
        now = timezone.now()
        change_order = ChangeOrder.objects.create(
            project=self.project,
            number=1,
            title='Add built-ins',
            description='Install built-ins',
            price_delta=Decimal('300.00'),
            status=ChangeOrder.Status.APPROVED,
            created_by=self.admin,
            submitted_by=self.admin,
            submitted_at=now,
            decided_by=self.client_user,
            decided_at=now,
        )
        invoice = create_invoice_from_change_order(
            change_order_id=change_order.pk,
            actor=self.admin,
            form_data={
                'title': 'Built-ins invoice',
                'due_date': timezone.localdate() + timedelta(days=30),
                'tax_rate': Decimal('0'),
                'notes': '',
            },
        )
        self.client.force_login(self.admin)
        void_url = reverse(
            'projects:change_order_void',
            args=(self.project.pk, change_order.pk),
        )

        self.assertEqual(
            self.client.post(void_url, {'reason': 'Scope removed'}).status_code,
            403,
        )
        self.client.post(
            reverse('billing:invoice_discard', args=(self.project.pk, invoice.pk)),
        )
        response = self.client.post(void_url, {'reason': 'Scope removed'})

        change_order.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(change_order.status, ChangeOrder.Status.VOIDED)
