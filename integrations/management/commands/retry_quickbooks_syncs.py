from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from integrations.customer_sync import (
    QuickBooksSyncError,
    retry_customer_sync_attempt,
)
from integrations.item_sync import retry_item_sync_attempt
from integrations.models import QuickBooksSyncAttempt


class Command(BaseCommand):
    help = 'Retry due, retryable QuickBooks synchronization failures (customers and items).'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=25)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        limit = max(1, options['limit'])
        candidates = QuickBooksSyncAttempt.objects.filter(
            entity_type__in=(
                QuickBooksSyncAttempt.EntityType.CUSTOMER,
                QuickBooksSyncAttempt.EntityType.ITEM,
            ),
            status=QuickBooksSyncAttempt.Status.FAILED,
            retryable=True,
            next_retry_at__lte=timezone.now(),
            attempt_number__lt=settings.QUICKBOOKS_SYNC_MAX_ATTEMPTS,
        ).order_by('next_retry_at')[:limit]
        if options['dry_run']:
            self.stdout.write(f'{len(candidates)} synchronization attempt(s) are due.')
            return

        succeeded = 0
        failed = 0
        skipped = 0
        for candidate in candidates:
            try:
                if candidate.entity_type == QuickBooksSyncAttempt.EntityType.ITEM:
                    result = retry_item_sync_attempt(candidate.pk, actor=None)
                else:
                    result = retry_customer_sync_attempt(candidate.pk, actor=None)
            except QuickBooksSyncError:
                skipped += 1
                continue
            if result.status == QuickBooksSyncAttempt.Status.SUCCEEDED:
                succeeded += 1
            else:
                failed += 1
        self.stdout.write(
            self.style.SUCCESS(
                f'Retries complete: {succeeded} succeeded, {failed} failed, '
                f'{skipped} skipped.'
            )
        )
