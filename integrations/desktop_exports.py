"""Durable duplicate guard for the future Desktop writer, not a sync service.

There is deliberately no HTTP endpoint or qbXML writer for these operations yet.
Once handed to a transport, an export stays uncertain until its exact request is
reconciled. A timeout must never make a second Add request eligible automatically.
"""
import json
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from billing.models import CreditMemo, Invoice
from projects.access import can_manage_organization
from projects.models import ChangeOrder, FinishSelection

from .desktop_models import DesktopConnection, DesktopExport
from .desktop_protocol import digest


def checked_connection(connection_id, actor):
    connection = DesktopConnection.objects.select_for_update().get(pk=connection_id)
    if not actor.is_authenticated or not actor.is_active or not can_manage_organization(actor, connection.organization):
        raise PermissionDenied
    if not connection.active or not connection.verified_at or connection.verified_at < timezone.now() - timedelta(minutes=15):
        raise ValidationError('Verify the Desktop company file before preparing a transfer.')
    return connection


def checked_source(kind, source_id, connection):
    model = {'invoice': Invoice, 'credit_memo': CreditMemo}.get(kind)
    if not model:
        raise ValidationError('Unsupported Desktop record type.')
    source = model.objects.select_for_update().filter(pk=source_id, organization_id=connection.organization_id).first()
    if not source or source.project.organization_id != connection.organization_id:
        raise PermissionDenied
    statuses = (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.PAID) if kind == 'invoice' else (CreditMemo.Status.ISSUED,)
    if source.status not in statuses or not source.number or not source.issued_at or not source.issued_by_id:
        raise ValidationError('Only issued records can be prepared for Desktop.')
    if source.source_change_order_id and source.source_change_order.status != ChangeOrder.Status.APPROVED:
        raise ValidationError('The source change order must be approved.')
    if kind == 'invoice' and source.source_selection_id and source.source_selection.status != FinishSelection.Status.SELECTED:
        raise ValidationError('The source selection must be completed.')
    return source


def content_digest(source):
    if isinstance(source, Invoice):
        values = {name: getattr(source, name) for name in (
            'project_id', 'number', 'title', 'notes', 'disclosure_snapshot',
            'issue_date', 'due_date', 'subtotal_amount', 'tax_rate', 'tax_amount', 'total_amount',
        )}
        values['lines'] = list(source.line_items.order_by('sort_order', 'pk').values(
            'description', 'quantity', 'unit_price', 'cost_code_id', 'category',
        ))
    else:
        values = {name: getattr(source, name) for name in ('project_id', 'number', 'amount', 'source_change_order_id')}
    return digest(json.dumps(values, default=str, sort_keys=True, separators=(',', ':')))


@transaction.atomic
def reserve_export(*, connection_id, kind, source_id, actor):
    connection = checked_connection(connection_id, actor)
    source = checked_source(kind, source_id, connection)
    fingerprint = content_digest(source)
    existing = DesktopExport.objects.filter(**{kind: source}).first()
    if existing:
        if (existing.connection_id != connection.pk or existing.content_digest != fingerprint
                or existing.company_file_digest != connection.company_file_digest):
            raise ValidationError('This record already has a different Desktop transfer. Reconcile it before proceeding.')
        return existing
    return DesktopExport.objects.create(
        connection=connection, **{kind: source}, content_digest=fingerprint,
        company_file_digest=connection.company_file_digest, prepared_by=actor,
    )


@transaction.atomic
def mark_delivery_started(*, export_id, actor):
    initial = DesktopExport.objects.get(pk=export_id)
    connection = checked_connection(initial.connection_id, actor)
    kind = 'invoice' if initial.invoice_id else 'credit_memo'
    source = checked_source(kind, initial.invoice_id or initial.credit_memo_id, connection)
    export = DesktopExport.objects.select_for_update().get(pk=export_id)
    if export.state != DesktopExport.State.PREPARED:
        raise ValidationError('This transfer cannot be sent again. Reconcile its result first.')
    if export.company_file_digest != connection.company_file_digest or export.content_digest != content_digest(source):
        raise ValidationError('The company file or accounting record changed after preparation.')
    export.state = DesktopExport.State.UNCERTAIN
    export.save(update_fields=['state', 'updated_at'])
    return export


@transaction.atomic
def record_receipt(*, export_id, connection_id, request_id, txn_id, edit_sequence):
    # Internal transport callback: binds every receipt to a company and request.
    connection = DesktopConnection.objects.select_for_update().get(pk=connection_id)
    export = DesktopExport.objects.select_for_update().get(pk=export_id)
    if (not connection.active or export.connection_id != connection.pk
            or export.company_file_digest != connection.company_file_digest
            or str(export.request_id) != str(request_id)):
        raise ValidationError('The receipt does not match the prepared transfer.')
    if not txn_id.strip() or len(txn_id) > 50 or not edit_sequence.strip() or len(edit_sequence) > 50:
        raise ValidationError('A valid transaction identity and edit sequence are required.')
    if export.state == DesktopExport.State.CONFIRMED:
        if (export.txn_id, export.edit_sequence) != (txn_id, edit_sequence):
            raise ValidationError('Conflicting Desktop receipt; manual reconciliation required.')
        return export
    if export.state != DesktopExport.State.UNCERTAIN:
        raise ValidationError('No delivery has been started for this record.')
    export.txn_id, export.edit_sequence = txn_id, edit_sequence
    export.state = DesktopExport.State.CONFIRMED
    export.save(update_fields=['txn_id', 'edit_sequence', 'state', 'updated_at'])
    return export
