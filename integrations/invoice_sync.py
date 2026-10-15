from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from billing.models import Invoice
from projects.models import ActivityEvent
from projects.services import record_activity

from .customer_sync import (
    QuickBooksSyncBusy,
    QuickBooksSyncError,
    _require_accounting_write,
)
from .models import (
    QuickBooksConnection,
    QuickBooksInvoiceMapping,
    QuickBooksItemMapping,
    QuickBooksProjectCustomerMapping,
    QuickBooksSyncAttempt,
)
from .quickbooks import QuickBooksAccountingClient, QuickBooksAPIError
from .services import QuickBooksMappingError, save_invoice_mapping
from .sync_execution import exclusive_execution
from .taxes import invoice_tax_mapping, validate_invoice_tax


def _invoice_snapshot(invoice):
    return {
        key: invoice.get(key)
        for key in (
            'Id',
            'SyncToken',
            'DocNumber',
            'CustomerRef',
            'TotalAmt',
            'Balance',
            'TxnDate',
            'DueDate',
            'TxnTaxDetail',
        )
        if key in invoice
    }


def _build_invoice_line_items(invoice, connection):
    lines = []
    unmapped = []
    for line in invoice.line_items.select_related('cost_code').all():
        item_mapping = None
        if line.cost_code_id:
            item_mapping = QuickBooksItemMapping.objects.filter(
                cost_code_id=line.cost_code_id,
                connection=connection,
                status=QuickBooksItemMapping.Status.ACTIVE,
            ).first()
        if not item_mapping:
            unmapped.append(line.cost_code.code if line.cost_code_id else line.description)
            continue
        lines.append(
            {
                'DetailType': 'SalesItemLineDetail',
                'Amount': str(line.total_amount),
                'Description': line.description,
                'SalesItemLineDetail': {
                    'ItemRef': {'value': item_mapping.quickbooks_item_id},
                    'Qty': str(line.quantity),
                    'UnitPrice': str(line.unit_price),
                },
            }
        )
        if connection.country == 'US':
            lines[-1]['SalesItemLineDetail']['TaxCodeRef'] = {
                'value': 'TAX' if invoice.tax_rate else 'NON'
            }
    if unmapped:
        raise QuickBooksSyncError(
            'Map every invoice line to a QuickBooks item before syncing. Unmapped: '
            + ', '.join(unmapped)
        )
    return lines


@transaction.atomic
def _create_running_attempt(
    *,
    connection,
    invoice,
    invoice_mapping,
    operation,
    direction,
    request_payload,
    actor,
    operation_key=None,
    attempt_number=1,
):
    # Recheck under the invoice lock: the initial screen/request checks may be stale.
    current = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if QuickBooksSyncAttempt.objects.filter(invoice=invoice, entity_type='invoice', status='running').exists():
        raise QuickBooksSyncBusy('An invoice synchronization is already running for this invoice.')
    if operation == QuickBooksSyncAttempt.Operation.CREATE and not operation_key:
        if current.status in (Invoice.Status.DRAFT, Invoice.Status.VOIDED):
            raise QuickBooksSyncError('Only issued invoices can be synchronized.')
        if QuickBooksInvoiceMapping.objects.filter(invoice=invoice).exists():
            raise QuickBooksSyncError('This invoice is already synchronized to QuickBooks.')
        if QuickBooksSyncAttempt.objects.filter(
            invoice=invoice, entity_type='invoice', operation='create',
            dispatched_at__isnull=False,
        ).exists():
            raise QuickBooksSyncError(
                'An earlier invoice request may already exist in QuickBooks. Retry or reconcile that saved request.'
            )
    values = {
        'connection': connection,
        'invoice': invoice,
        'invoice_mapping': invoice_mapping,
        'project': invoice.project,
        'entity_type': QuickBooksSyncAttempt.EntityType.INVOICE,
        'operation': operation,
        'direction': direction,
        'status': QuickBooksSyncAttempt.Status.RUNNING,
        'attempt_number': attempt_number,
        'request_payload': request_payload,
        'requested_by': actor,
    }
    if operation_key:
        values['operation_key'] = operation_key
    try:
        return QuickBooksSyncAttempt.objects.create(**values)
    except IntegrityError as exc:
        raise QuickBooksSyncBusy(
            'An invoice synchronization is already running for this QuickBooks company.'
        ) from exc


def start_invoice_sync(*, invoice_id, connection_id, actor, api_client=None):
    invoice = Invoice.objects.select_related('organization', 'project').get(pk=invoice_id)
    connection = QuickBooksConnection.objects.get(pk=connection_id)
    if invoice.organization_id != connection.organization_id:
        raise QuickBooksSyncError(
            'The invoice and QuickBooks connection must belong to the same company.'
        )
    if connection.status != QuickBooksConnection.Status.CONNECTED:
        raise QuickBooksSyncError('Reconnect QuickBooks before synchronizing invoices.')
    if invoice.status == Invoice.Status.DRAFT:
        raise QuickBooksSyncError('Issue the invoice before synchronizing it to QuickBooks.')
    if invoice.status == Invoice.Status.VOIDED:
        raise QuickBooksSyncError('A voided invoice cannot be synchronized to QuickBooks.')
    tax_mapping = None
    if invoice.tax_rate:
        try:
            tax_mapping = invoice_tax_mapping(invoice, connection)
        except QuickBooksMappingError as exc:
            raise QuickBooksSyncError(str(exc)) from exc
    existing_mapping = QuickBooksInvoiceMapping.objects.filter(
        invoice=invoice,
        status=QuickBooksInvoiceMapping.Status.ACTIVE,
    ).first()
    if existing_mapping:
        raise QuickBooksSyncError('This invoice is already synchronized to QuickBooks.')
    customer_mapping = QuickBooksProjectCustomerMapping.objects.filter(
        project=invoice.project,
        connection=connection,
        status=QuickBooksProjectCustomerMapping.Status.ACTIVE,
    ).first()
    if not customer_mapping:
        raise QuickBooksSyncError(
            'Map this project to a QuickBooks customer before synchronizing invoices.'
        )
    line_items = _build_invoice_line_items(invoice, connection)

    payload = {
        'CustomerRef': {'value': customer_mapping.quickbooks_customer_id},
        'Line': line_items,
    }
    if connection.country != 'US':
        payload['GlobalTaxCalculation'] = 'TaxExcluded'
    if invoice.issue_date:
        payload['TxnDate'] = invoice.issue_date.isoformat()
    if invoice.due_date:
        payload['DueDate'] = invoice.due_date.isoformat()
    if tax_mapping:
        payload['TxnTaxDetail'] = {'TxnTaxCodeRef': {'value': tax_mapping.quickbooks_tax_code_id}}

    attempt = _create_running_attempt(
        connection=connection,
        invoice=invoice,
        invoice_mapping=None,
        operation=QuickBooksSyncAttempt.Operation.CREATE,
        direction=QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS,
        request_payload=payload,
        actor=actor,
    )
    return execute_invoice_sync_attempt(attempt.pk, api_client=api_client)


def start_invoice_void_sync(*, invoice_id, connection_id, actor, api_client=None):
    invoice = Invoice.objects.select_related('organization', 'project').get(pk=invoice_id)
    connection = QuickBooksConnection.objects.get(pk=connection_id)
    if invoice.organization_id != connection.organization_id:
        raise QuickBooksSyncError(
            'The invoice and QuickBooks connection must belong to the same company.'
        )
    if connection.status != QuickBooksConnection.Status.CONNECTED:
        raise QuickBooksSyncError('Reconnect QuickBooks before synchronizing invoices.')
    if invoice.status != Invoice.Status.VOIDED:
        raise QuickBooksSyncError('Only a locally voided invoice can be voided in QuickBooks.')
    mapping = QuickBooksInvoiceMapping.objects.filter(
        invoice=invoice,
        connection=connection,
        status=QuickBooksInvoiceMapping.Status.ACTIVE,
    ).first()
    if not mapping:
        raise QuickBooksSyncError('This invoice has no active QuickBooks mapping to void.')

    attempt = _create_running_attempt(
        connection=connection,
        invoice=invoice,
        invoice_mapping=mapping,
        operation=QuickBooksSyncAttempt.Operation.VOID,
        direction=QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS,
        request_payload={'quickbooks_invoice_id': mapping.quickbooks_invoice_id},
        actor=actor,
    )
    return execute_invoice_sync_attempt(attempt.pk, api_client=api_client)


@exclusive_execution
def execute_invoice_sync_attempt(attempt_id, *, api_client=None):
    attempt = QuickBooksSyncAttempt.objects.select_related(
        'connection', 'invoice__organization', 'invoice__project', 'invoice_mapping'
    ).get(pk=attempt_id)
    if attempt.status != QuickBooksSyncAttempt.Status.RUNNING:
        raise QuickBooksSyncError('Only running synchronization attempts can execute.')
    api_client = api_client or QuickBooksAccountingClient()
    try:
        result = _perform_invoice_operation(attempt, api_client)
        # Checkpoint the committed remote identity before local validation/storage.
        attempt.external_id = str(result.get('Id') or '')
        attempt.external_sync_token = str(result.get('SyncToken') or '')
        attempt.response_snapshot = _invoice_snapshot(result)
        attempt.save(update_fields=('external_id', 'external_sync_token', 'response_snapshot'))
        if attempt.operation == QuickBooksSyncAttempt.Operation.VOID:
            with transaction.atomic():
                mapping = QuickBooksInvoiceMapping.objects.select_for_update().get(
                    pk=attempt.invoice_mapping_id
                )
                mapping.mark_voided()
                if result.get('SyncToken') is not None:
                    mapping.quickbooks_sync_token = str(result['SyncToken'])
                mapping.save()
        else:
            mapping = save_invoice_mapping(
                invoice=attempt.invoice,
                connection=attempt.connection,
                quickbooks_invoice=result,
                actor=attempt.requested_by,
            )
            try:
                remote_tax = Decimal(str((result.get('TxnTaxDetail') or {}).get('TotalTax', 0)))
            except (InvalidOperation, TypeError, ValueError):
                remote_tax = Decimal('NaN')
            if (mapping.external_total_amount != attempt.invoice.total_amount
                    or not remote_tax.is_finite() or remote_tax != attempt.invoice.tax_amount):
                # The remote write already committed. Retain its identity so an
                # administrator can reconcile it without creating a duplicate.
                attempt.invoice_mapping = mapping
                attempt.save(update_fields=('invoice_mapping',))
                raise QuickBooksMappingError(
                    'QuickBooks created the invoice with a different total or sales tax. '
                    'Review and reconcile both invoices before continuing.'
                )
    except QuickBooksAPIError as exc:
        return _mark_attempt_failed(attempt.pk, exc)
    except QuickBooksMappingError as exc:
        return _mark_attempt_failed(attempt.pk, exc)
    return _mark_attempt_succeeded(attempt.pk, mapping, result)


def _perform_invoice_operation(attempt, api_client):
    if attempt.operation == QuickBooksSyncAttempt.Operation.CREATE:
        mapping = QuickBooksInvoiceMapping.objects.filter(invoice=attempt.invoice).first()
        if mapping and mapping.connection_id != attempt.connection_id:
            raise QuickBooksMappingError('The saved invoice belongs to a different QuickBooks connection.')
        remote_id = attempt.external_id or (mapping.quickbooks_invoice_id if mapping else '')
        if remote_id:
            return api_client.get_invoice(attempt.connection, remote_id)
        if attempt.invoice.status in (Invoice.Status.DRAFT, Invoice.Status.VOIDED):
            raise QuickBooksMappingError('Review the local invoice status before recovering this request.')
        validate_invoice_tax(attempt.invoice, attempt.connection, attempt.request_payload, api_client)
        _require_accounting_write(attempt.connection)
        QuickBooksSyncAttempt.objects.filter(pk=attempt.pk, dispatched_at__isnull=True).update(dispatched_at=timezone.now())
        return api_client.create_invoice(
            attempt.connection,
            attempt.request_payload,
            request_id=attempt.request_id,
        )
    if attempt.operation == QuickBooksSyncAttempt.Operation.VOID:
        _require_accounting_write(attempt.connection)
        current = api_client.get_invoice(
            attempt.connection,
            attempt.request_payload['quickbooks_invoice_id'],
        )
        if Decimal(str(current.get('TotalAmt'))) == 0 and Decimal(str(current.get('Balance'))) == 0:
            return current
        QuickBooksSyncAttempt.objects.filter(pk=attempt.pk, dispatched_at__isnull=True).update(dispatched_at=timezone.now())
        return api_client.void_invoice(
            attempt.connection,
            {'Id': current.get('Id'), 'SyncToken': current.get('SyncToken')},
            request_id=attempt.request_id,
        )
    raise QuickBooksSyncError('Unsupported invoice synchronization operation.')


@transaction.atomic
def _mark_attempt_succeeded(attempt_id, mapping, result):
    attempt = QuickBooksSyncAttempt.objects.select_for_update().get(pk=attempt_id)
    now = timezone.now()
    attempt.invoice_mapping = mapping
    attempt.status = QuickBooksSyncAttempt.Status.SUCCEEDED
    attempt.response_snapshot = _invoice_snapshot(result)
    attempt.external_id = str(result.get('Id') or '')
    attempt.external_sync_token = str(result.get('SyncToken') or '')
    attempt.completed_at = now
    attempt.error_code = ''
    attempt.error_message = ''
    attempt.retryable = False
    attempt.next_retry_at = None
    attempt.save()
    QuickBooksSyncAttempt.objects.filter(
        connection=attempt.connection,
        operation_key=attempt.operation_key,
        status=QuickBooksSyncAttempt.Status.FAILED,
    ).exclude(pk=attempt.pk).update(
        status=QuickBooksSyncAttempt.Status.RESOLVED,
        resolution_note=f'Superseded by successful attempt #{attempt.attempt_number}.',
        resolved_at=now,
    )
    record_activity(
        organization=attempt.invoice.organization,
        project=attempt.invoice.project,
        actor=attempt.requested_by,
        event_type=ActivityEvent.Type.QUICKBOOKS_INVOICE_SYNC_SUCCEEDED,
        summary=(
            f'QuickBooks invoice sync succeeded for {attempt.invoice.display_number} '
            f'(attempt {attempt.attempt_number}).'
        ),
        metadata={
            'sync_attempt_id': attempt.pk,
            'operation': attempt.operation,
            'quickbooks_invoice_id': attempt.external_id,
        },
    )
    return attempt


@transaction.atomic
def _mark_attempt_failed(attempt_id, error):
    attempt = QuickBooksSyncAttempt.objects.select_for_update().get(pk=attempt_id)
    now = timezone.now()
    attempt.status = QuickBooksSyncAttempt.Status.FAILED
    attempt.error_code = getattr(error, 'code', 'mapping_error')
    attempt.error_message = getattr(error, 'public_message', str(error))[:255]
    attempt.retryable = bool(getattr(error, 'retryable', False))
    if attempt.retryable and attempt.attempt_number < settings.QUICKBOOKS_SYNC_MAX_ATTEMPTS:
        delay = settings.QUICKBOOKS_SYNC_RETRY_BASE_SECONDS * (
            2 ** (attempt.attempt_number - 1)
        )
        attempt.next_retry_at = now + timedelta(seconds=min(delay, 3600))
    else:
        attempt.next_retry_at = None
    attempt.completed_at = now
    attempt.save()
    record_activity(
        organization=attempt.invoice.organization,
        project=attempt.invoice.project,
        actor=attempt.requested_by,
        event_type=ActivityEvent.Type.QUICKBOOKS_INVOICE_SYNC_FAILED,
        summary=(
            f'QuickBooks invoice sync failed for {attempt.invoice.display_number} '
            f'(attempt {attempt.attempt_number}).'
        ),
        metadata={
            'sync_attempt_id': attempt.pk,
            'operation': attempt.operation,
            'error_code': attempt.error_code,
            'retryable': attempt.retryable,
        },
    )
    return attempt


@transaction.atomic
def prepare_invoice_sync_retry(attempt_id, *, actor):
    previous = QuickBooksSyncAttempt.objects.select_for_update().get(pk=attempt_id)
    latest = QuickBooksSyncAttempt.objects.filter(
        connection=previous.connection,
        operation_key=previous.operation_key,
    ).order_by('-attempt_number').first()
    if latest.pk != previous.pk or previous.status != QuickBooksSyncAttempt.Status.FAILED:
        raise QuickBooksSyncError('Only the latest failed attempt can be retried.')
    if not previous.retryable:
        raise QuickBooksSyncError('This failure requires review instead of automatic retry.')
    if previous.attempt_number >= settings.QUICKBOOKS_SYNC_MAX_ATTEMPTS:
        raise QuickBooksSyncError('This synchronization reached its retry limit.')

    previous.status = QuickBooksSyncAttempt.Status.RESOLVED
    previous.resolution_note = f'Retry attempt #{previous.attempt_number + 1} started.'
    previous.resolved_by = actor
    previous.resolved_at = timezone.now()
    previous.save()
    retry = _create_running_attempt(
        connection=previous.connection,
        invoice=previous.invoice,
        invoice_mapping=previous.invoice_mapping,
        operation=previous.operation,
        direction=previous.direction,
        request_payload=previous.request_payload,
        actor=actor,
        operation_key=previous.operation_key,
        attempt_number=previous.attempt_number + 1,
    )
    retry.external_id = previous.external_id
    retry.external_sync_token = previous.external_sync_token
    retry.response_snapshot = previous.response_snapshot
    retry.dispatched_at = previous.dispatched_at
    retry.save(update_fields=('external_id', 'external_sync_token', 'response_snapshot', 'dispatched_at'))
    return retry


def retry_invoice_sync_attempt(attempt_id, *, actor=None, api_client=None):
    attempt = prepare_invoice_sync_retry(attempt_id, actor=actor)
    return execute_invoice_sync_attempt(attempt.pk, api_client=api_client)


@transaction.atomic
def resolve_invoice_sync_attempt(attempt_id, *, actor, note):
    attempt = QuickBooksSyncAttempt.objects.select_for_update().get(pk=attempt_id)
    if attempt.status != QuickBooksSyncAttempt.Status.FAILED:
        raise QuickBooksSyncError('Only failed synchronization attempts can be resolved.')
    note = note.strip()
    if not note:
        raise QuickBooksSyncError('Describe how the synchronization issue was resolved.')
    attempt.status = QuickBooksSyncAttempt.Status.RESOLVED
    attempt.resolution_note = note[:255]
    attempt.resolved_by = actor
    attempt.resolved_at = timezone.now()
    attempt.next_retry_at = None
    attempt.save()
    record_activity(
        organization=attempt.invoice.organization,
        project=attempt.invoice.project,
        actor=actor,
        event_type=ActivityEvent.Type.QUICKBOOKS_SYNC_RESOLVED,
        summary=f'QuickBooks sync issue was resolved for {attempt.invoice.display_number}.',
        metadata={'sync_attempt_id': attempt.pk, 'error_code': attempt.error_code},
    )
    return attempt
