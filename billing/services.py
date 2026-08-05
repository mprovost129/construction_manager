from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Sum
from django.urls import reverse
from django.utils import timezone

from projects.models import ActivityEvent, ChangeOrder, Organization
from projects.money import money
from projects.services import (
    document_client_recipients,
    record_activity,
    send_notification_email,
)

from .models import CreditMemo, Invoice, InvoiceLineItem, Payment


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
        tax_rate=form_data['tax_rate'],
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
def create_credit_memo_from_change_order(*, change_order_id, actor):
    change_order = ChangeOrder.objects.select_for_update().select_related(
        'project__organization'
    ).get(pk=change_order_id)
    if change_order.status != ChangeOrder.Status.APPROVED:
        raise ValidationError('Only approved change orders can become a credit memo.')
    if change_order.price_delta >= 0:
        raise ValidationError(
            'Only a change order with a client credit can become a credit memo.'
        )
    if hasattr(change_order, 'credit_memo'):
        raise ValidationError('This change order already has a credit memo.')
    credit_memo = CreditMemo(
        organization=change_order.project.organization,
        project=change_order.project,
        source_change_order=change_order,
        amount=abs(change_order.price_delta),
        created_by=actor,
    )
    credit_memo.full_clean()
    credit_memo.save()
    record_activity(
        organization=credit_memo.organization,
        project=credit_memo.project,
        actor=actor,
        event_type=ActivityEvent.Type.CREDIT_MEMO_CREATED,
        summary=(
            f'{actor.email} created a credit memo draft from {change_order.display_number}.'
        ),
        metadata={'credit_memo_id': credit_memo.pk, 'change_order_id': change_order.pk},
    )
    return credit_memo


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
def issue_credit_memo(*, credit_memo_id, actor):
    credit_memo = CreditMemo.objects.select_for_update().select_related(
        'organization', 'project'
    ).get(pk=credit_memo_id)
    if credit_memo.status != CreditMemo.Status.DRAFT:
        raise ValidationError('Only a draft credit memo can be issued.')
    Organization.objects.select_for_update().get(pk=credit_memo.organization_id)
    current_number = credit_memo.organization.credit_memos.aggregate(
        maximum=Max('number')
    )['maximum']
    now = timezone.now()
    credit_memo.number = (current_number or 0) + 1
    credit_memo.status = CreditMemo.Status.ISSUED
    credit_memo.issued_by = actor
    credit_memo.issued_at = now
    credit_memo.full_clean()
    credit_memo.save()
    record_activity(
        organization=credit_memo.organization,
        project=credit_memo.project,
        actor=actor,
        event_type=ActivityEvent.Type.CREDIT_MEMO_ISSUED,
        summary=(
            f'{actor.email} issued {credit_memo.display_number} for '
            f'${credit_memo.amount:,.2f}.'
        ),
        metadata={
            'credit_memo_id': credit_memo.pk,
            'number': credit_memo.number,
            'amount': str(credit_memo.amount),
        },
    )
    return credit_memo


@transaction.atomic
def void_credit_memo(*, credit_memo_id, actor, reason):
    credit_memo = CreditMemo.objects.select_for_update().select_related(
        'organization', 'project'
    ).get(pk=credit_memo_id)
    if credit_memo.status != CreditMemo.Status.ISSUED:
        raise ValidationError('Only an issued credit memo can be voided locally.')
    if credit_memo.remaining_balance != credit_memo.amount:
        raise ValidationError('A credit memo with any applied amount cannot be voided locally.')
    credit_memo.status = CreditMemo.Status.VOIDED
    credit_memo.voided_by = actor
    credit_memo.voided_at = timezone.now()
    credit_memo.void_reason = reason.strip()
    credit_memo.full_clean()
    credit_memo.save()
    record_activity(
        organization=credit_memo.organization,
        project=credit_memo.project,
        actor=actor,
        event_type=ActivityEvent.Type.CREDIT_MEMO_VOIDED,
        summary=f'{actor.email} voided {credit_memo.display_number}.',
        metadata={
            'credit_memo_id': credit_memo.pk,
            'number': credit_memo.number,
            'reason': credit_memo.void_reason,
        },
    )
    return credit_memo


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


def _apply_payment_state(invoice):
    total_paid = money(
        invoice.payments.aggregate(total=Sum('amount'))['total'] or 0
    )
    invoice.amount_paid = total_paid
    if total_paid <= 0:
        invoice.status = Invoice.Status.ISSUED
    elif total_paid >= invoice.total_amount:
        invoice.status = Invoice.Status.PAID
    else:
        invoice.status = Invoice.Status.PARTIALLY_PAID
    invoice.full_clean()
    invoice.save(update_fields=('amount_paid', 'status', 'updated_at'))


@transaction.atomic
def record_payment(
    *, invoice_id, actor, amount, method, reference, paid_date, note, credit_memo=None
):
    invoice = Invoice.objects.select_for_update().select_related(
        'organization', 'project'
    ).get(pk=invoice_id)
    if invoice.status not in (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID):
        raise ValidationError('Only an issued or partially paid invoice can receive payments.')
    amount = money(amount)
    if amount <= 0:
        raise ValidationError({'amount': 'Payment amount must be greater than zero.'})
    if amount > invoice.balance_due:
        raise ValidationError({'amount': 'Payment cannot exceed the balance due.'})
    payment = Payment(
        invoice=invoice,
        amount=amount,
        method=method,
        credit_memo=credit_memo,
        reference=reference,
        paid_date=paid_date,
        note=note,
        recorded_by=actor,
    )
    payment.full_clean()
    payment.save()
    _apply_payment_state(invoice)
    record_activity(
        organization=invoice.organization,
        project=invoice.project,
        actor=actor,
        event_type=ActivityEvent.Type.PAYMENT_RECORDED,
        summary=(
            f'{actor.email} recorded a ${payment.amount:,.2f} payment on '
            f'{invoice.display_number}.'
        ),
        metadata={'invoice_id': invoice.pk, 'payment_id': payment.pk, 'amount': str(payment.amount)},
    )
    return payment


@transaction.atomic
def delete_payment(*, payment_id, actor):
    payment = Payment.objects.select_related('invoice__organization', 'invoice__project').get(
        pk=payment_id
    )
    invoice = Invoice.objects.select_for_update().select_related(
        'organization', 'project'
    ).get(pk=payment.invoice_id)
    if invoice.status == Invoice.Status.VOIDED:
        raise ValidationError('Payments on a voided invoice cannot be modified.')
    amount = payment.amount
    payment.delete()
    _apply_payment_state(invoice)
    record_activity(
        organization=invoice.organization,
        project=invoice.project,
        actor=actor,
        event_type=ActivityEvent.Type.PAYMENT_DELETED,
        summary=(
            f'{actor.email} removed a ${amount:,.2f} payment from '
            f'{invoice.display_number}.'
        ),
        metadata={'invoice_id': invoice.pk, 'amount': str(amount)},
    )
    return invoice


@transaction.atomic
def apply_credit_memo(*, credit_memo_id, invoice_id, amount, actor):
    credit_memo = CreditMemo.objects.select_for_update().select_related(
        'organization', 'project'
    ).get(pk=credit_memo_id)
    if credit_memo.status != CreditMemo.Status.ISSUED:
        raise ValidationError('Only an issued credit memo can be applied.')
    amount = money(amount)
    if amount <= 0:
        raise ValidationError({'amount': 'Applied amount must be greater than zero.'})
    if amount > credit_memo.remaining_balance:
        raise ValidationError(
            {'amount': "The amount exceeds the credit memo's remaining balance."}
        )
    if not Invoice.objects.filter(pk=invoice_id, project_id=credit_memo.project_id).exists():
        raise ValidationError('The invoice must belong to the credit memo project.')
    payment = record_payment(
        invoice_id=invoice_id,
        actor=actor,
        amount=amount,
        method=Payment.Method.CREDIT_MEMO,
        reference=credit_memo.display_number,
        paid_date=timezone.localdate(),
        note=f'Applied from {credit_memo.display_number}.',
        credit_memo=credit_memo,
    )
    record_activity(
        organization=credit_memo.organization,
        project=credit_memo.project,
        actor=actor,
        event_type=ActivityEvent.Type.CREDIT_MEMO_APPLIED,
        summary=(
            f'{actor.email} applied ${amount:,.2f} of {credit_memo.display_number} to '
            f'{payment.invoice.display_number}.'
        ),
        metadata={
            'credit_memo_id': credit_memo.pk,
            'invoice_id': invoice_id,
            'payment_id': payment.pk,
            'amount': str(amount),
        },
    )
    return payment


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
