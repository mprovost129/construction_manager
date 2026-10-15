import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from integrations.customer_sync import QuickBooksSyncError
from integrations.reconciliation import due_payment_count, reconcile_due_payments


class Command(BaseCommand):
    help = 'Check enabled companies for payment changes (read-only in QuickBooks).'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=25, help='Invoices per company per pass (1-100).')
        parser.add_argument('--loop', action='store_true', help='Run continuously, checking due work every minute.')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        if not 1 <= options['limit'] <= 100:
            raise CommandError('Limit must be between 1 and 100.')
        if options['dry_run']:
            self.stdout.write(f'{due_payment_count()} mapped invoice(s) due; no changes made.')
            return
        try:
            while True:
                close_old_connections()
                try:
                    results = reconcile_due_payments(limit=options['limit'])
                except QuickBooksSyncError as exc:
                    raise CommandError(str(exc)) from exc
                self.stdout.write(f'Payment checks: {results}', ending='\n')
                self.stdout.flush()
                if not options['loop']:
                    break
                time.sleep(60)
        except KeyboardInterrupt:
            self.stdout.write('Payment worker stopped.')
        finally:
            close_old_connections()
