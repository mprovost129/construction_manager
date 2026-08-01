from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.views import View
from django.views.generic import FormView, TemplateView

from projects.access import can_manage_project, is_project_client
from projects.models import ChangeOrder

from .access import (
    invoice_project_or_404,
    require_invoice_manager,
    visible_invoice_or_404,
    visible_invoices,
)
from .forms import InvoiceDraftForm, InvoiceLineItemForm, InvoiceVoidForm
from .models import Invoice, InvoiceLineItem
from .pdf import build_invoice_pdf
from .services import (
    create_invoice_from_change_order,
    discard_invoice_draft,
    issue_invoice,
    record_invoice_draft_created,
    send_invoice_issued_notification,
    void_invoice,
)


def validation_message(error):
    return error.messages[0] if error.messages else 'The invoice could not be updated.'


class InvoiceListView(LoginRequiredMixin, TemplateView):
    template_name = 'billing/invoice_list.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = invoice_project_or_404(request.user, kwargs['project_id'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        invoices = visible_invoices(self.request.user, self.project)
        context.update(
            {
                'project': self.project,
                'invoices': invoices,
                'can_manage_invoices': can_manage_project(
                    self.request.user,
                    self.project,
                ),
                'viewer_is_client': is_project_client(
                    self.request.user,
                    self.project,
                ),
                'total_invoiced': sum(
                    (
                        invoice.total_amount
                        for invoice in invoices
                        if invoice.status != Invoice.Status.VOIDED
                    ),
                    0,
                ),
                'total_balance': sum(
                    (
                        invoice.balance_due
                        for invoice in invoices
                        if invoice.status != Invoice.Status.VOIDED
                    ),
                    0,
                ),
            }
        )
        return context


class InvoiceCreateView(LoginRequiredMixin, FormView):
    form_class = InvoiceDraftForm
    template_name = 'billing/invoice_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = invoice_project_or_404(request.user, kwargs['project_id'])
        require_invoice_manager(request.user, self.project)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({'project': self.project, 'invoice': None, 'source': None})
        return context

    def form_valid(self, form):
        invoice = form.save(commit=False)
        invoice.organization = self.project.organization
        invoice.project = self.project
        invoice.created_by = self.request.user
        invoice.full_clean()
        invoice.save()
        record_invoice_draft_created(invoice, self.request.user)
        messages.success(self.request, 'Invoice draft created. Add at least one line item.')
        return redirect('billing:invoice_detail', self.project.pk, invoice.pk)


class InvoiceFromChangeOrderCreateView(LoginRequiredMixin, FormView):
    form_class = InvoiceDraftForm
    template_name = 'billing/invoice_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = invoice_project_or_404(request.user, kwargs['project_id'])
        require_invoice_manager(request.user, self.project)
        self.change_order = get_object_or_404(
            ChangeOrder,
            project=self.project,
            pk=kwargs['change_order_id'],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        initial['title'] = (
            f'{self.change_order.display_number} - {self.change_order.title}'
        )
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'project': self.project,
                'invoice': None,
                'source': self.change_order,
            }
        )
        return context

    def form_valid(self, form):
        try:
            invoice = create_invoice_from_change_order(
                change_order_id=self.change_order.pk,
                actor=self.request.user,
                form_data=form.cleaned_data,
            )
        except ValidationError as exc:
            form.add_error(None, validation_message(exc))
            return self.form_invalid(form)
        messages.success(
            self.request,
            f'Invoice draft created from {self.change_order.display_number}.',
        )
        return redirect('billing:invoice_detail', self.project.pk, invoice.pk)


class InvoiceDetailView(LoginRequiredMixin, TemplateView):
    template_name = 'billing/invoice_detail.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = invoice_project_or_404(request.user, kwargs['project_id'])
        self.invoice = visible_invoice_or_404(
            request.user,
            self.project,
            kwargs['invoice_id'],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        can_manage = can_manage_project(self.request.user, self.project)
        question_subject = f'Question about {self.invoice.display_number}'
        context.update(
            {
                'project': self.project,
                'invoice': self.invoice,
                'line_items': self.invoice.line_items.all(),
                'can_manage_invoices': can_manage,
                'viewer_is_client': is_project_client(
                    self.request.user,
                    self.project,
                ),
                'void_form': (
                    InvoiceVoidForm()
                    if can_manage and self.invoice.status == Invoice.Status.ISSUED
                    else None
                ),
                'question_url': (
                    f'{reverse("projects:message_create", args=(self.project.pk,))}'
                    f'?{urlencode({"subject": question_subject})}'
                ),
                'quickbooks_mapping': (
                    getattr(self.invoice, 'quickbooks_mapping', None)
                    if can_manage
                    else None
                ),
            }
        )
        return context


class InvoicePDFDownloadView(LoginRequiredMixin, View):
    def get(self, request, project_id, invoice_id):
        project = invoice_project_or_404(request.user, project_id)
        invoice = visible_invoice_or_404(request.user, project, invoice_id)
        filename = (
            f'{invoice.display_number.lower()}-invoice.pdf'
            if invoice.number is not None
            else f'draft-invoice-{invoice.pk}.pdf'
        )
        response = HttpResponse(
            build_invoice_pdf(invoice),
            content_type='application/pdf',
        )
        response.headers['Content-Disposition'] = content_disposition_header(
            True,
            filename,
        )
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response


class InvoiceUpdateView(LoginRequiredMixin, FormView):
    form_class = InvoiceDraftForm
    template_name = 'billing/invoice_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = invoice_project_or_404(request.user, kwargs['project_id'])
        require_invoice_manager(request.user, self.project)
        self.invoice = get_object_or_404(
            Invoice,
            project=self.project,
            pk=kwargs['invoice_id'],
            status=Invoice.Status.DRAFT,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.invoice
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {'project': self.project, 'invoice': self.invoice, 'source': None}
        )
        return context

    def form_valid(self, form):
        with transaction.atomic():
            invoice = Invoice.objects.select_for_update().get(
                pk=self.invoice.pk,
                project=self.project,
                status=Invoice.Status.DRAFT,
            )
            for field_name in ('title', 'due_date', 'tax_amount', 'notes'):
                setattr(invoice, field_name, form.cleaned_data[field_name])
            invoice.recalculate_totals(save=False)
            invoice.full_clean()
            invoice.save()
        messages.success(self.request, 'Invoice draft updated.')
        return redirect('billing:invoice_detail', self.project.pk, invoice.pk)


class InvoiceLineItemCreateView(LoginRequiredMixin, FormView):
    form_class = InvoiceLineItemForm
    template_name = 'billing/invoice_line_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = invoice_project_or_404(request.user, kwargs['project_id'])
        require_invoice_manager(request.user, self.project)
        self.invoice = get_object_or_404(
            Invoice,
            project=self.project,
            pk=kwargs['invoice_id'],
            status=Invoice.Status.DRAFT,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {'project': self.project, 'invoice': self.invoice, 'line_item': None}
        )
        return context

    def form_valid(self, form):
        with transaction.atomic():
            invoice = Invoice.objects.select_for_update().get(
                pk=self.invoice.pk,
                project=self.project,
                status=Invoice.Status.DRAFT,
            )
            line = form.save(commit=False)
            line.invoice = invoice
            line.full_clean()
            line.save()
            invoice.recalculate_totals()
        messages.success(self.request, 'Invoice line added.')
        return redirect('billing:invoice_detail', self.project.pk, self.invoice.pk)


class InvoiceLineItemUpdateView(LoginRequiredMixin, FormView):
    form_class = InvoiceLineItemForm
    template_name = 'billing/invoice_line_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = invoice_project_or_404(request.user, kwargs['project_id'])
        require_invoice_manager(request.user, self.project)
        self.invoice = get_object_or_404(
            Invoice,
            project=self.project,
            pk=kwargs['invoice_id'],
            status=Invoice.Status.DRAFT,
        )
        self.line_item = get_object_or_404(
            InvoiceLineItem,
            invoice=self.invoice,
            pk=kwargs['line_item_id'],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.line_item
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'project': self.project,
                'invoice': self.invoice,
                'line_item': self.line_item,
            }
        )
        return context

    def form_valid(self, form):
        with transaction.atomic():
            invoice = Invoice.objects.select_for_update().get(
                pk=self.invoice.pk,
                project=self.project,
                status=Invoice.Status.DRAFT,
            )
            line = InvoiceLineItem.objects.select_for_update().get(
                pk=self.line_item.pk,
                invoice=invoice,
            )
            for field_name in (
                'category',
                'description',
                'quantity',
                'unit_price',
                'sort_order',
            ):
                setattr(line, field_name, form.cleaned_data[field_name])
            line.full_clean()
            line.save()
            invoice.recalculate_totals()
        messages.success(self.request, 'Invoice line updated.')
        return redirect('billing:invoice_detail', self.project.pk, self.invoice.pk)


class InvoiceLineItemDeleteView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, project_id, invoice_id, line_item_id):
        project = invoice_project_or_404(request.user, project_id)
        require_invoice_manager(request.user, project)
        invoice = get_object_or_404(
            Invoice,
            project=project,
            pk=invoice_id,
            status=Invoice.Status.DRAFT,
        )
        with transaction.atomic():
            invoice = Invoice.objects.select_for_update().get(
                pk=invoice.pk,
                project=project,
                status=Invoice.Status.DRAFT,
            )
            line = get_object_or_404(
                InvoiceLineItem.objects.select_for_update(),
                invoice=invoice,
                pk=line_item_id,
            )
            line.delete()
            invoice.recalculate_totals()
        messages.success(request, 'Invoice line removed.')
        return redirect('billing:invoice_detail', project.pk, invoice.pk)


class InvoiceIssueView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, project_id, invoice_id):
        project = invoice_project_or_404(request.user, project_id)
        require_invoice_manager(request.user, project)
        invoice = get_object_or_404(Invoice, project=project, pk=invoice_id)
        try:
            invoice = issue_invoice(invoice_id=invoice.pk, actor=request.user)
        except ValidationError as exc:
            messages.error(request, validation_message(exc))
        else:
            delivery_result = send_invoice_issued_notification(request, invoice)
            if delivery_result is None:
                messages.warning(
                    request,
                    'The invoice was issued, but its notification could not be sent.',
                )
            else:
                messages.success(request, f'{invoice.display_number} was issued.')
        return redirect('billing:invoice_detail', project.pk, invoice.pk)


class InvoiceDiscardView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, project_id, invoice_id):
        project = invoice_project_or_404(request.user, project_id)
        require_invoice_manager(request.user, project)
        invoice = get_object_or_404(Invoice, project=project, pk=invoice_id)
        try:
            discard_invoice_draft(invoice_id=invoice.pk, actor=request.user)
        except ValidationError as exc:
            messages.error(request, validation_message(exc))
            return redirect('billing:invoice_detail', project.pk, invoice.pk)
        messages.success(request, 'Invoice draft discarded.')
        return redirect('billing:invoice_list', project.pk)


class InvoiceVoidView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, project_id, invoice_id):
        project = invoice_project_or_404(request.user, project_id)
        require_invoice_manager(request.user, project)
        invoice = get_object_or_404(Invoice, project=project, pk=invoice_id)
        form = InvoiceVoidForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Provide a reason before voiding the invoice.')
        else:
            try:
                invoice = void_invoice(
                    invoice_id=invoice.pk,
                    actor=request.user,
                    reason=form.cleaned_data['reason'],
                )
            except ValidationError as exc:
                messages.error(request, validation_message(exc))
            else:
                messages.success(request, f'{invoice.display_number} was voided.')
        return redirect('billing:invoice_detail', project.pk, invoice.pk)
