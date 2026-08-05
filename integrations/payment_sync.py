from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from billing.models import Invoice, Payment
from billing.services import record_payment
from projects.models import ActivityEvent
from projects.services import record_activity

from .customer_sync import QuickBooksSyncBusy, QuickBooksSyncError
from .models import (
    QuickBooksConnection,
    QuickBooksCreditMemoMapping,
    QuickBooksInvoiceMapping,
    QuickBooksPaymentMapping,
    QuickBooksSyncAttempt,
)
from .quickbooks import QuickBooksAccountingClient, QuickBooksAPIError
from .services import (
    QuickBooksMappingError,
    save_credit_memo_mapping,
    save_payment_mapping,
)

_METHOD_KEYWORDS = {
    Payment.Method.CHECK: ('check',),
    Payment.Method.ACH: ('ach', 'bank transfer', 'eft', 'e-check'),
    Payment.Method.CARD: ('credit card', 'card', 'visa', 'mastercard', 'amex', 'discover'),
    Payment.Method.CASH: ('cash',),
}


def _method_from_quickbooks(payment_method_ref):
    name = ''
    if isinstance(payment_method_ref, dict):
        name = str(payment_method_ref.get('name') or '').lower()
    for method, keywords in _METHOD_KEYWORDS.items():
        if any(keyword in name for keyword in keywords):
            return method
    return Payment.Method.OTHER


def _find_possible_duplicate(invoice, amount, paid_date):
    if amount is None or not paid_date:
        return None
    amount = Decimal(str(amount))
    txn_date = date.fromisoformat(str(paid_date)[:10])
    return Payment.objects.filter(
        invoice=invoice,
        amount=amount,
        paid_date__gte=txn_date - timedelta(days=3),
        paid_date__lte=txn_date + timedelta(days=3),
        quickbooks_mapping__isnull=True,
        quickbooks_credit_memo_mapping__isnull=True,
    ).first()


def _create_running_attempt(
    *,
    connection,
    invoice,
    operation,
    direction,
    request_payload,
    actor,
    operation_key=None,
    attempt_number=1,
):
    values = {
        'connection': connection,
        'invoice': invoice,
        'project': invoice.project,
        'entity_type': QuickBooksSyncAttempt.EntityType.PAYMENT,
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
            'A payment synchronization is already running for this QuickBooks company.'
        ) from exc


def start_invoice_payment_sync(*, invoice_id, connection_id, actor, api_client=None):
    invoice = Invoice.objects.select_related('organization', 'project').get(pk=invoice_id)
    connection = QuickBooksConnection.objects.get(pk=connection_id)
    if invoice.organization_id != connection.organization_id:
        raise QuickBooksSyncError(
            'The invoice and QuickBooks connection must belong to the same company.'
        )
    if connection.status != QuickBooksConnection.Status.CONNECTED:
        raise QuickBooksSyncError('Reconnect QuickBooks before synchronizing payments.')
    invoice_mapping = QuickBooksInvoiceMapping.objects.filter(
        invoice=invoice,
        connection=connection,
        status=QuickBooksInvoiceMapping.Status.ACTIVE,
    ).first()
    if not invoice_mapping:
        raise QuickBooksSyncError(
            'Synchronize this invoice to QuickBooks before importing its payments.'
        )

    attempt = _create_running_attempt(
        connection=connection,
        invoice=invoice,
        operation=QuickBooksSyncAttempt.Operation.READ,
        direction=QuickBooksSyncAttempt.Direction.FROM_QUICKBOOKS,
        request_payload={'quickbooks_invoice_id': invoice_mapping.quickbooks_invoice_id},
        actor=actor,
    )
    return execute_invoice_payment_sync_attempt(attempt.pk, api_client=api_client)


def execute_invoice_payment_sync_attempt(attempt_id, *, api_client=None):
    attempt = QuickBooksSyncAttempt.objects.select_related(
        'connection', 'invoice__organization', 'invoice__project'
    ).get(pk=attempt_id)
    if attempt.status != QuickBooksSyncAttempt.Status.RUNNING:
        raise QuickBooksSyncError('Only running synchronization attempts can execute.')
    api_client = api_client or QuickBooksAccountingClient()
    invoice = attempt.invoice
    connection = attempt.connection

    try:
        invoice_mapping = QuickBooksInvoiceMapping.objects.get(
            invoice=invoice,
            connection=connection,
            status=QuickBooksInvoiceMapping.Status.ACTIVE,
        )

        reverified = []
        for mapping in QuickBooksPaymentMapping.objects.filter(
            invoice_mapping=invoice_mapping,
            status=QuickBooksPaymentMapping.Status.ACTIVE,
        ):
            try:
                payment = api_client.get_payment(connection, mapping.quickbooks_payment_id)
            except QuickBooksAPIError as exc:
                if not exc.is_not_found:
                    raise
                with transaction.atomic():
                    stale = QuickBooksPaymentMapping.objects.select_for_update().get(
                        pk=mapping.pk
                    )
                    stale.mark_tombstoned()
                    stale.save()
                reverified.append(
                    {'quickbooks_payment_id': mapping.quickbooks_payment_id, 'status': 'tombstoned'}
                )
                continue
            save_payment_mapping(
                invoice_mapping=invoice_mapping,
                connection=connection,
                quickbooks_payment=payment,
                local_payment=mapping.payment,
                actor=attempt.requested_by,
            )
            reverified.append(
                {'quickbooks_payment_id': mapping.quickbooks_payment_id, 'status': 'updated'}
            )

        mapped_ids = set(
            QuickBooksPaymentMapping.objects.filter(
                invoice_mapping=invoice_mapping,
            ).values_list('quickbooks_payment_id', flat=True)
        )
        found = api_client.find_payments_for_invoice(
            connection,
            invoice_mapping.quickbooks_customer_id,
            invoice_mapping.quickbooks_invoice_id,
        )
        created = []
        possible_duplicates = []
        for qb_payment in found:
            payment_id = str(qb_payment.get('Id') or '')
            if not payment_id or payment_id in mapped_ids:
                continue
            amount = qb_payment.get('TotalAmt')
            paid_date = qb_payment.get('TxnDate')
            duplicate = _find_possible_duplicate(invoice, amount, paid_date)
            if duplicate:
                possible_duplicates.append(
                    {'quickbooks_payment_id': payment_id, 'local_payment_id': duplicate.pk}
                )
                continue
            local_payment = record_payment(
                invoice_id=invoice.pk,
                actor=attempt.requested_by,
                amount=amount,
                method=_method_from_quickbooks(qb_payment.get('PaymentMethodRef')),
                reference=str(qb_payment.get('PaymentRefNum') or ''),
                paid_date=paid_date,
                note=f'Imported from QuickBooks payment {payment_id}.',
            )
            save_payment_mapping(
                invoice_mapping=invoice_mapping,
                connection=connection,
                quickbooks_payment=qb_payment,
                local_payment=local_payment,
                actor=attempt.requested_by,
            )
            created.append(payment_id)

        credit_memos_reverified = []
        for mapping in QuickBooksCreditMemoMapping.objects.filter(
            invoice_mapping=invoice_mapping,
            status=QuickBooksCreditMemoMapping.Status.ACTIVE,
        ):
            try:
                credit_memo = api_client.get_credit_memo(
                    connection, mapping.quickbooks_credit_memo_id
                )
            except QuickBooksAPIError as exc:
                if not exc.is_not_found:
                    raise
                with transaction.atomic():
                    stale = QuickBooksCreditMemoMapping.objects.select_for_update().get(
                        pk=mapping.pk
                    )
                    stale.mark_tombstoned()
                    stale.save()
                credit_memos_reverified.append(
                    {
                        'quickbooks_credit_memo_id': mapping.quickbooks_credit_memo_id,
                        'status': 'tombstoned',
                    }
                )
                continue
            save_credit_memo_mapping(
                invoice_mapping=invoice_mapping,
                connection=connection,
                quickbooks_credit_memo=credit_memo,
                local_payment=mapping.payment,
                actor=attempt.requested_by,
            )
            credit_memos_reverified.append(
                {
                    'quickbooks_credit_memo_id': mapping.quickbooks_credit_memo_id,
                    'status': 'updated',
                }
            )

        mapped_credit_memo_ids = set(
            QuickBooksCreditMemoMapping.objects.filter(
                invoice_mapping=invoice_mapping,
            ).values_list('quickbooks_credit_memo_id', flat=True)
        )
        found_credit_memos = api_client.find_credit_memos_for_invoice(
            connection,
            invoice_mapping.quickbooks_customer_id,
            invoice_mapping.quickbooks_invoice_id,
        )
        credit_memos_created = []
        credit_memo_possible_duplicates = []
        for qb_credit_memo in found_credit_memos:
            credit_memo_id = str(qb_credit_memo.get('Id') or '')
            if not credit_memo_id or credit_memo_id in mapped_credit_memo_ids:
                continue
            amount = qb_credit_memo.get('TotalAmt')
            txn_date = qb_credit_memo.get('TxnDate')
            duplicate = _find_possible_duplicate(invoice, amount, txn_date)
            if duplicate:
                credit_memo_possible_duplicates.append(
                    {
                        'quickbooks_credit_memo_id': credit_memo_id,
                        'local_payment_id': duplicate.pk,
                    }
                )
                continue
            local_payment = record_payment(
                invoice_id=invoice.pk,
                actor=attempt.requested_by,
                amount=amount,
                method=Payment.Method.CREDIT_MEMO,
                reference=str(qb_credit_memo.get('DocNumber') or ''),
                paid_date=txn_date,
                note=f'Imported from QuickBooks credit memo {credit_memo_id}.',
            )
            save_credit_memo_mapping(
                invoice_mapping=invoice_mapping,
                connection=connection,
                quickbooks_credit_memo=qb_credit_memo,
                local_payment=local_payment,
                actor=attempt.requested_by,
            )
            credit_memos_created.append(credit_memo_id)
    except (QuickBooksAPIError, QuickBooksMappingError, ValidationError) as exc:
        return _mark_attempt_failed(attempt.pk, exc)

    summary = {
        'created': created,
        'credit_memos_created': credit_memos_created,
        'reverified': reverified,
        'credit_memos_reverified': credit_memos_reverified,
        'possible_duplicates': possible_duplicates,
        'credit_memo_possible_duplicates': credit_memo_possible_duplicates,
    }
    if possible_duplicates or credit_memo_possible_duplicates:
        ids = ', '.join(
            entry['quickbooks_payment_id'] for entry in possible_duplicates
        )
        credit_memo_ids = ', '.join(
            entry['quickbooks_credit_memo_id'] for entry in credit_memo_possible_duplicates
        )
        messages = [
            part
            for part in (
                f'payment(s) {ids}' if ids else '',
                f'credit memo(s) {credit_memo_ids}' if credit_memo_ids else '',
            )
            if part
        ]
        return _mark_attempt_failed(
            attempt.pk,
            QuickBooksSyncError(
                f'Possible duplicate QuickBooks {" and ".join(messages)} need review.'
            ),
            response_snapshot=summary,
        )
    return _mark_attempt_succeeded(attempt.pk, summary)


@transaction.atomic
def _mark_attempt_succeeded(attempt_id, summary):
    attempt = QuickBooksSyncAttempt.objects.select_for_update().get(pk=attempt_id)
    now = timezone.now()
    attempt.status = QuickBooksSyncAttempt.Status.SUCCEEDED
    attempt.response_snapshot = summary
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
    return attempt


@transaction.atomic
def _mark_attempt_failed(attempt_id, error, *, response_snapshot=None):
    attempt = QuickBooksSyncAttempt.objects.select_for_update().get(pk=attempt_id)
    now = timezone.now()
    attempt.status = QuickBooksSyncAttempt.Status.FAILED
    attempt.error_code = getattr(error, 'code', 'mapping_error')
    attempt.error_message = getattr(error, 'public_message', str(error))[:255]
    attempt.retryable = bool(getattr(error, 'retryable', False))
    if response_snapshot is not None:
        attempt.response_snapshot = response_snapshot
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
        event_type=ActivityEvent.Type.QUICKBOOKS_PAYMENT_SYNC_FAILED,
        summary=(
            f'QuickBooks payment sync failed for {attempt.invoice.display_number} '
            f'(attempt {attempt.attempt_number}).'
        ),
        metadata={
            'sync_attempt_id': attempt.pk,
            'error_code': attempt.error_code,
            'retryable': attempt.retryable,
        },
    )
    return attempt


@transaction.atomic
def prepare_invoice_payment_sync_retry(attempt_id, *, actor):
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
    return _create_running_attempt(
        connection=previous.connection,
        invoice=previous.invoice,
        operation=previous.operation,
        direction=previous.direction,
        request_payload=previous.request_payload,
        actor=actor,
        operation_key=previous.operation_key,
        attempt_number=previous.attempt_number + 1,
    )


def retry_invoice_payment_sync_attempt(attempt_id, *, actor=None, api_client=None):
    attempt = prepare_invoice_payment_sync_retry(attempt_id, actor=actor)
    return execute_invoice_payment_sync_attempt(attempt.pk, api_client=api_client)


@transaction.atomic
def resolve_invoice_payment_sync_attempt(attempt_id, *, actor, note):
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
