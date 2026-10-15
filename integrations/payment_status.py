"""Read-only invoice payment-check status; rendering never contacts QuickBooks."""

from django.conf import settings
from django.utils import timezone

from .models import QuickBooksSyncAttempt


def invoice_payment_status(mapping):
    attempts = QuickBooksSyncAttempt.objects.filter(
        connection_id=mapping.connection_id, invoice_id=mapping.invoice_id,
        entity_type='payment', direction='from_quickbooks',
    )
    verified = attempts.filter(
        status='succeeded', request_payload__verify_invoice=True,
        request_payload__quickbooks_invoice_id=mapping.quickbooks_invoice_id,
        completed_at__isnull=False,
    ).order_by('-completed_at', '-pk').first()
    failed = attempts.filter(status='failed').order_by('-created_at', '-pk').first()
    running = attempts.filter(status='running').exists()
    connection = mapping.connection
    enabled = connection.automatic_payment_updates
    configured = (
        settings.QUICKBOOKS_CONFIGURED
        and connection.environment == settings.QUICKBOOKS_ENVIRONMENT
    )
    active = mapping.status == 'active' and mapping.invoice.status in (
        'issued', 'partially_paid', 'paid',
    )
    can_poll = active and configured and connection.status == 'connected' and enabled
    stale = can_poll and connection.payment_worker_is_stale
    retry_at = (
        failed.next_retry_at
        if can_poll and failed and failed.retryable
        and failed.request_payload.get('verify_invoice')
        and failed.attempt_number < settings.QUICKBOOKS_SYNC_MAX_ATTEMPTS
        else None
    )
    result = {
        'last_verified_at': verified.completed_at if verified else None,
        'error_message': failed.error_message if failed else '',
        'automatic_enabled': enabled,
        'worker_stale': stale,
        'retry_at': retry_at,
        'next_check_at': mapping.next_payment_check_at if can_poll and not failed else None,
        'tone': 'on_hold',
    }
    if not active:
        result.update(label='Payment checks inactive', explanation='This invoice is no longer eligible for automatic payment checks.')
    elif connection.status != 'connected':
        result.update(label='Reconnect QuickBooks', explanation='A company administrator must reconnect QuickBooks before payments can be checked.')
    elif not configured:
        result.update(label='Payment checks unavailable', explanation='QuickBooks configuration must be completed for this company.')
    elif running:
        result.update(label='Checking payments', explanation='A payment check is in progress. Refresh this page to see its result.')
    elif failed:
        if retry_at:
            result.update(label='Payment check awaiting retry', explanation='The previous check failed. Automatic updates will retry when due and the worker is available.')
        else:
            result.update(label='Payment check needs review', explanation='A company administrator must review the unresolved check before automatic updates can resume.', tone='declined')
    elif not enabled:
        result.update(label='Automatic payment checks paused', explanation='A company administrator can enable automatic updates from the QuickBooks dashboard.')
    elif stale:
        result.update(label='Payment checks delayed', explanation='The background worker has not checked in recently. New payment changes may not be reflected here yet.')
    elif mapping.next_payment_check_at <= timezone.now():
        result.update(label='Payment check queued', explanation='This invoice is due for its next payment check.')
    else:
        result.update(label='Payment checks scheduled', explanation='Automatic checks include paid invoices so payment reversals can be detected.', tone='active')
    return result
