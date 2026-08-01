from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from billing.models import Invoice, money
from projects.models import ActivityEvent
from projects.services import record_activity

from .models import (
    QuickBooksConnection,
    QuickBooksInvoiceMapping,
    QuickBooksProjectCustomerMapping,
)
from .services import QuickBooksMappingError


def _invoice_snapshot(invoice):
    snapshot = {
        key: invoice.get(key)
        for key in (
            'Id',
            'SyncToken',
            'DocNumber',
            'TxnDate',
            'DueDate',
            'TotalAmt',
            'Balance',
        )
        if key in invoice
    }
    for reference_name in ('CustomerRef', 'CurrencyRef'):
        reference = invoice.get(reference_name)
        if isinstance(reference, dict):
            snapshot[reference_name] = {
                key: reference.get(key)
                for key in ('value', 'name')
                if key in reference
            }
    linked_transactions = []
    for linked in invoice.get('LinkedTxn') or []:
        if isinstance(linked, dict):
            linked_transactions.append(
                {
                    key: linked.get(key)
                    for key in ('TxnId', 'TxnType')
                    if key in linked
                }
            )
    if linked_transactions:
        snapshot['LinkedTxn'] = linked_transactions
    return snapshot


def _optional_money(invoice, field_name):
    value = invoice.get(field_name)
    if value is None or value == '':
        return None
    try:
        parsed = money(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise QuickBooksMappingError(
            f'QuickBooks returned an invalid {field_name} value.'
        ) from exc
    if parsed < 0:
        raise QuickBooksMappingError(
            f'QuickBooks returned an invalid {field_name} value.'
        )
    return parsed


def _optional_date(invoice, field_name):
    value = invoice.get(field_name)
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise QuickBooksMappingError(
            f'QuickBooks returned an invalid {field_name} value.'
        ) from exc


@transaction.atomic
def save_invoice_mapping(*, invoice, connection, quickbooks_invoice, actor):
    if invoice.organization_id != connection.organization_id:
        raise QuickBooksMappingError(
            'The invoice and QuickBooks connection must belong to the same company.'
        )
    if invoice.status == Invoice.Status.DRAFT:
        raise QuickBooksMappingError(
            'Issue the local invoice before assigning a QuickBooks identity.'
        )
    if connection.status != QuickBooksConnection.Status.CONNECTED:
        raise QuickBooksMappingError(
            'Reconnect the QuickBooks company before saving an invoice mapping.'
        )

    external_id = str(quickbooks_invoice.get('Id') or '').strip()
    sync_token = str(quickbooks_invoice.get('SyncToken') or '').strip()
    customer_ref = quickbooks_invoice.get('CustomerRef')
    customer_id = (
        str(customer_ref.get('value') or '').strip()
        if isinstance(customer_ref, dict)
        else ''
    )
    if not external_id or not sync_token or not customer_id:
        raise QuickBooksMappingError(
            'QuickBooks returned incomplete invoice identity information.'
        )

    customer_mapping = QuickBooksProjectCustomerMapping.objects.filter(
        project=invoice.project,
        connection=connection,
        status=QuickBooksProjectCustomerMapping.Status.ACTIVE,
    ).first()
    if not customer_mapping:
        raise QuickBooksMappingError(
            'Map the invoice project to an active QuickBooks customer first.'
        )
    if customer_mapping.quickbooks_customer_id != customer_id:
        raise QuickBooksMappingError(
            'The QuickBooks invoice customer does not match the project mapping.'
        )

    external_conflict = QuickBooksInvoiceMapping.objects.filter(
        connection=connection,
        quickbooks_invoice_id=external_id,
    ).exclude(invoice=invoice)
    if external_conflict.exists():
        raise QuickBooksMappingError(
            'That QuickBooks invoice is already mapped to another local invoice.'
        )

    total_amount = _optional_money(quickbooks_invoice, 'TotalAmt')
    balance = _optional_money(quickbooks_invoice, 'Balance')
    if total_amount is not None and balance is not None and balance > total_amount:
        raise QuickBooksMappingError(
            'QuickBooks returned an invoice balance greater than its total.'
        )
    txn_date = _optional_date(quickbooks_invoice, 'TxnDate')
    due_date = _optional_date(quickbooks_invoice, 'DueDate')
    currency_ref = quickbooks_invoice.get('CurrencyRef')
    currency_code = (
        str(currency_ref.get('value') or '')[:10]
        if isinstance(currency_ref, dict)
        else ''
    )
    now = timezone.now()

    try:
        mapping, created = QuickBooksInvoiceMapping.objects.select_for_update().get_or_create(
            invoice=invoice,
            defaults={
                'connection': connection,
                'quickbooks_invoice_id': external_id,
                'quickbooks_sync_token': sync_token,
                'quickbooks_customer_id': customer_id,
            },
        )
    except IntegrityError as exc:
        raise QuickBooksMappingError(
            'That QuickBooks invoice is already mapped to another local invoice.'
        ) from exc

    if not created and (
        mapping.connection_id != connection.pk
        or mapping.quickbooks_invoice_id != external_id
    ):
        raise QuickBooksMappingError(
            'A local invoice cannot be reassigned to a different QuickBooks invoice.'
        )

    mapping.quickbooks_sync_token = sync_token
    mapping.quickbooks_customer_id = customer_id
    mapping.quickbooks_doc_number = str(
        quickbooks_invoice.get('DocNumber') or ''
    )[:50]
    mapping.status = QuickBooksInvoiceMapping.Status.ACTIVE
    mapping.external_total_amount = total_amount
    mapping.external_balance = balance
    mapping.external_txn_date = txn_date
    mapping.external_due_date = due_date
    mapping.currency_code = currency_code
    mapping.last_synced_values = _invoice_snapshot(quickbooks_invoice)
    mapping.last_synced_at = now
    mapping.last_seen_at = now
    mapping.external_voided_at = None
    mapping.tombstoned_at = None
    mapping.full_clean()
    mapping.save()

    if created:
        record_activity(
            organization=invoice.organization,
            project=invoice.project,
            actor=actor,
            event_type=ActivityEvent.Type.QUICKBOOKS_INVOICE_MAPPED,
            summary=(
                f'{invoice.display_number} was mapped to QuickBooks invoice '
                f'{mapping.quickbooks_doc_number or external_id}.'
            ),
            metadata={
                'invoice_id': invoice.pk,
                'quickbooks_invoice_id': external_id,
                'realm_id': connection.realm_id,
            },
        )
    return mapping
