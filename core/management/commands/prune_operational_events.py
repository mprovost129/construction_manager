from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import OperationalEvent


class Command(BaseCommand):
    help = 'Remove operational signals older than the retention window (default 30 days).'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        if options['days'] < 1:
            raise CommandError('Retention must be at least one day.')
        events = OperationalEvent.objects.filter(
            created_at__lt=timezone.now() - timedelta(days=options['days'])
        )
        if options['dry_run']:
            self.stdout.write(f'{events.count()} operational events eligible for removal.')
        else:
            count, _ = events.delete()
            self.stdout.write(f'Removed {count} operational events. Project audit history is unchanged.')
