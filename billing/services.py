from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from projects.models import ActivityEvent, ChangeOrder, Organization
from projects.services import (
    document_client_recipients,
    record_activity,
    send_notification_email,
)

from .models import Invoice, InvoiceLineItem


def record_invoice_draft_created(invoice, actor):
    record_activity(
        organization=invoice.organization,
        project=invoice.project,
        actor=actor,
        event_type=ActivityEvent.Type.INVOICE_DRAFT_CREATED,
        summary=f'{actor.email} created a draft invoice: {invoice.title}.',
        metadata={'invoice_id': invoice.pk},
    )


@transaction.atomic
def discard_invoice_draft(*, invoice_id, actor):
    invoice = Invoice.objects.select_for_update().select_related(
        'organization', 'project'
    ).get(pk=invoice_id)
    if invoice.status != Invoice.Status.DRAFT:
        raise ValidationError('Only a draft invoice can be discarded.')
    project = invoice.project
    invoice_title = invoice.title
    record_activity(
        organization=invoice.organization,
        project=project,
        actor=actor,
        event_type=ActivityEvent.Type.INVOICE_DRAFT_DISCARDED,
        summary=f'{actor.email} discarded the invoice draft: {invoice_title}.',
        metadata={'invoice_id': invoice.pk, 'title': invoice_title},
    )
    invoice.delete()
    return project


@transaction.atomic
def create_invoice_from_change_order(*, change_order_id, actor, form_data):
    change_order = ChangeOrder.objects.select_for_update().select_related(
        'project__organization'
    ).get(pk=change_order_id)
    if change_order.status != ChangeOrder.Status.APPROVED:
        raise ValidationError('Only approved change orders can be invoiced.')
    if hasattr(change_order, 'invoice'):
        raise ValidationError('This change order already has an invoice.')
    if change_order.price_delta <= 0:
        raise ValidationError(
            'A zero or negative change order requires the future credit-memo workflow.'
        )
    invoice = Invoice(
        organization=change_order.project.organization,
        project=change_order.project,
        source_change_order=change_order,
        created_by=actor,
        title=form_data['title'],
        due_date=form_data['due_date'],
        tax_amount=form_data['tax_amount'],
        notes=form_data['notes'],
    )
    invoice.full_clean()
    invoice.save()
    source_lines = list(change_order.line_items.all())
    if source_lines:
        for source in source_lines:
            line = InvoiceLineItem(
                invoice=invoice,
                category=source.category,
                description=source.description,
                quantity=source.quantity,
                unit_price=source.unit_price,
                sort_order=source.sort_order,
            )
            line.full_clean()
            line.save()
    else:
        line = InvoiceLineItem(
            invoice=invoice,
            description=f'{change_order.display_number} - {change_order.title}',
            quantity=1,
            unit_price=change_order.price_delta,
        )
        line.full_clean()
        line.save()
    invoice.recalculate_totals()
    record_invoice_draft_created(invoice, actor)
    return invoice


@transaction.atomic
def issue_invoice(*, invoice_id, actor):
    invoice = Invoice.objects.select_for_update().select_related(
        'organization', 'project'
    ).get(pk=invoice_id)
    if invoice.status != Invoice.Status.DRAFT:
        raise ValidationError('Only draft invoices can be issued.')
    if not invoice.line_items.exists():
        raise ValidationError('Add at least one line item before issuing the invoice.')
    if not invoice.due_date:
        raise ValidationError('Set a due date before issuing the invoice.')
    if not document_client_recipients(invoice.project):
        raise ValidationError('Assign an active client before issuing the invoice.')

    invoice.recalculate_totals(save=False)
    if invoice.total_amount <= 0:
        raise ValidationError('An invoice must have a positive total before issue.')
    Organization.objects.select_for_update().get(pk=invoice.organization_id)
    current_number = invoice.organization.invoices.aggregate(
        maximum=Max('number')
    )['maximum']
    now = timezone.now()
    invoice.number = (current_number or 0) + 1
    invoice.status = Invoice.Status.ISSUED
    invoice.issue_date = timezone.localdate()
    invoice.issued_by = actor
    invoice.issued_at = now
    invoice.full_clean()
    invoice.save()
    record_activity(
        organization=invoice.organization,
        project=invoice.project,
        actor=actor,
        event_type=ActivityEvent.Type.INVOICE_ISSUED,
        summary=(
            f'{actor.email} issued {invoice.display_number} for '
            f'${invoice.total_amount:,.2f}.'
        ),
        metadata={
            'invoice_id': invoice.pk,
            'number': invoice.number,
            'total': str(invoice.total_amount),
        },
    )
    return invoice


@transaction.atomic
def void_invoice(*, invoice_id, actor, reason):
    invoice = Invoice.objects.select_for_update().select_related(
        'organization', 'project'
    ).get(pk=invoice_id)
    if invoice.status != Invoice.Status.ISSUED:
        raise ValidationError('Only an unpaid issued invoice can be voided locally.')
    if invoice.amount_paid:
        raise ValidationError('An invoice with payments cannot be voided locally.')
    invoice.status = Invoice.Status.VOIDED
    invoice.voided_by = actor
    invoice.voided_at = timezone.now()
    invoice.void_reason = reason.strip()
    invoice.full_clean()
    invoice.save()
    record_activity(
        organization=invoice.organization,
        project=invoice.project,
        actor=actor,
        event_type=ActivityEvent.Type.INVOICE_VOIDED,
        summary=f'{actor.email} voided {invoice.display_number}.',
        metadata={
            'invoice_id': invoice.pk,
            'number': invoice.number,
            'reason': invoice.void_reason,
        },
    )
    return invoice


def send_invoice_issued_notification(request, invoice):
    recipients = document_client_recipients(invoice.project)
    if not recipients:
        return 0
    detail_url = request.build_absolute_uri(
        reverse('billing:invoice_detail', args=(invoice.project_id, invoice.pk))
    )
    return send_notification_email(
        subject=(
            f'Invoice available - {invoice.project.name}: {invoice.display_number}'
        ),
        message=(
            f'{invoice.display_number} - {invoice.title} is available for '
            f'{invoice.project.name}.\n\n'
            f'Total: ${invoice.total_amount:,.2f}\n'
            f'Due: {invoice.due_date:%B %d, %Y}\n\n'
            f'View invoice: {detail_url}\n\n'
            'Online payment is not available in Construction Manager.'
        ),
        recipient_list=recipients,
    )
