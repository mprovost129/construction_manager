"""Opt-in, bounded polling of mapped invoices. Never writes to QuickBooks."""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone

from .customer_sync import QuickBooksSyncBusy, QuickBooksSyncError
from .models import (
    QuickBooksConnection,
    QuickBooksInvoiceMapping,
    QuickBooksSyncAttempt,
)
from .payment_sync import (
    execute_invoice_payment_sync_attempt,
    retry_invoice_payment_sync_attempt,
    start_invoice_payment_sync,
)
from .quickbooks import QuickBooksAccountingClient
from .sync_execution import attempt_execution_lock

CHECK_INTERVAL = timedelta(minutes=15)


def verify_remote_invoice(remote, mapping, *, check_balance=False):
    """Do not import payments against a changed/voided accounting document."""
    invoice = mapping.invoice
    invoice.refresh_from_db()
    try:
        total = Decimal(str(remote['TotalAmt']))
        tax = Decimal(str((remote.get('TxnTaxDetail') or {}).get('TotalTax', 0)))
        balance = Decimal(str(remote['Balance']))
        valid = all(
            value.is_finite() and value >= 0 and value == value.quantize(Decimal('.01'))
            for value in (total, tax, balance)
        )
    except (KeyError, TypeError, ValueError, InvalidOperation):
        valid = False
    if not valid:
        raise QuickBooksSyncError('QuickBooks returned invalid invoice amounts. Review this invoice.')
    if (
        str(remote.get('Id')) != mapping.quickbooks_invoice_id
        or remote.get('SyncToken') is None
        or str((remote.get('CustomerRef') or {}).get('value')) != mapping.quickbooks_customer_id
        or (mapping.currency_code and
            str((remote.get('CurrencyRef') or {}).get('value')) != mapping.currency_code)
        or total != invoice.total_amount
        or tax != invoice.tax_amount
        or invoice.status not in ('issued', 'partially_paid', 'paid')
        or balance > total
    ):
        raise QuickBooksSyncError(
            'The QuickBooks invoice identity, total, tax, or local status has changed. '
            'Review both invoices before importing payments.'
        )
    if check_balance and balance != invoice.balance_due:
        raise QuickBooksSyncError(
            'The QuickBooks balance differs from the local payment ledger. '
            'Review payment and credit applications before resuming automatic updates.'
        )


def eligible_connections():
    return QuickBooksConnection.objects.filter(
        automatic_payment_updates=True, status='connected',
        environment=settings.QUICKBOOKS_ENVIRONMENT,
    ).order_by('pk')


def _schedule(mapping, attempt):
    now = timezone.now()
    QuickBooksInvoiceMapping.objects.filter(pk=mapping.pk).update(
        last_payment_check_at=now,
        next_payment_check_at=(attempt.next_retry_at or now + CHECK_INTERVAL),
    )


def _run_company(connection_id, *, limit, api):
    counts = {'succeeded': 0, 'failed': 0, 'review': 0, 'deferred': 0}
    if not eligible_connections().filter(pk=connection_id).exists():
        return counts
    # A killed process leaves its durable attempt running. Recover payment reads
    # only; outbound invoice/customer/item operations require their own workflow.
    running = QuickBooksSyncAttempt.objects.filter(
        connection_id=connection_id, entity_type='payment',
        direction='from_quickbooks', status='running',
    ).first()
    if running:
        cutoff = timezone.now() - CHECK_INTERVAL
        if (running.last_execution_at or running.started_at) > cutoff:
            counts['deferred'] += 1
            return counts
        try:
            recovered = execute_invoice_payment_sync_attempt(
                running.pk, api_client=api, recovery_before=cutoff,
            )
        except QuickBooksSyncBusy:
            counts['deferred'] += 1
            return counts
        except QuickBooksSyncError:
            counts['failed'] += 1
        else:
            mapping = QuickBooksInvoiceMapping.objects.filter(
                connection_id=connection_id, invoice_id=running.invoice_id,
            ).first()
            if mapping:
                _schedule(mapping, recovered)
            counts['succeeded' if recovered.status == 'succeeded' else 'failed'] += 1
        limit -= 1
        if not limit:
            return counts
    mappings = QuickBooksInvoiceMapping.objects.filter(
        connection_id=connection_id, status='active',
        invoice__status__in=('issued', 'partially_paid', 'paid'),
        next_payment_check_at__lte=timezone.now(),
    ).order_by('next_payment_check_at', 'pk')[:limit]
    for mapping in mappings:
        if not eligible_connections().filter(pk=connection_id).exists():
            break
        # Never bypass unresolved failures by issuing a new operation every tick.
        failed = QuickBooksSyncAttempt.objects.filter(
            connection_id=connection_id, invoice_id=mapping.invoice_id,
            entity_type='payment', direction='from_quickbooks', status='failed',
        ).order_by('-created_at', '-pk').first()
        if failed and (
            not failed.request_payload.get('verify_invoice')
            or not failed.retryable or failed.next_retry_at is None
            or failed.attempt_number >= settings.QUICKBOOKS_SYNC_MAX_ATTEMPTS
        ):
            counts['review'] += 1
            QuickBooksInvoiceMapping.objects.filter(pk=mapping.pk).update(
                next_payment_check_at=timezone.now() + CHECK_INTERVAL,
            )
            continue
        if failed and failed.next_retry_at > timezone.now():
            counts['deferred'] += 1
            QuickBooksInvoiceMapping.objects.filter(pk=mapping.pk).update(
                next_payment_check_at=failed.next_retry_at,
            )
            continue
        try:
            if failed:
                result = retry_invoice_payment_sync_attempt(failed.pk, api_client=api)
            else:
                result = start_invoice_payment_sync(
                    invoice_id=mapping.invoice_id, connection_id=connection_id,
                    actor=None, api_client=api, verify_invoice=True,
                )
        except QuickBooksSyncBusy:
            counts['deferred'] += 1
            break
        except QuickBooksSyncError:
            counts['failed'] += 1
            QuickBooksInvoiceMapping.objects.filter(pk=mapping.pk).update(
                next_payment_check_at=timezone.now() + CHECK_INTERVAL,
            )
            continue
        _schedule(mapping, result)
        counts['succeeded' if result.status == 'succeeded' else 'failed'] += 1
    return counts


def reconcile_due_payments(*, limit=25, api_client=None):
    """At most limit due invoices per company; oldest due invoices go first."""
    if limit < 1 or limit > 100:
        raise ValueError('Limit must be between 1 and 100.')
    if not settings.QUICKBOOKS_CONFIGURED:
        raise QuickBooksSyncError('QuickBooks must be configured before starting automatic updates.')
    results = {}
    for connection_id in eligible_connections().values_list('pk', flat=True):
        try:
            # Distinct namespace from per-attempt execution locks.
            with attempt_execution_lock(connection_id, namespace=0x5143):
                counts = _run_company(
                    connection_id, limit=limit,
                    api=api_client or QuickBooksAccountingClient(),
                )
                review_count = QuickBooksSyncAttempt.objects.filter(
                    connection_id=connection_id, entity_type='payment', status='failed',
                ).count()
                counts['unresolved'] = review_count
                QuickBooksConnection.objects.filter(pk=connection_id).update(
                    payment_worker_seen_at=timezone.now(), payment_worker_summary=counts,
                )
                results[connection_id] = counts
        except QuickBooksSyncBusy:
            results[connection_id] = {'busy': True}
    return results


def due_payment_count():
    return QuickBooksInvoiceMapping.objects.filter(
        connection__in=eligible_connections(), status='active',
        invoice__status__in=('issued', 'partially_paid', 'paid'),
        next_payment_check_at__lte=timezone.now(),
    ).count()
