from datetime import timezone as datetime_timezone

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from projects.staff_messaging import process_email_queue


class Command(BaseCommand):
    help = 'Process queued staff email, and daily deadlines after 14:00 UTC. Run every five minutes.'

    def handle(self, *args, **options):
        accepted, skipped, failed = process_email_queue()
        self.stdout.write(f'Staff email alerts: {accepted} accepted, {skipped} skipped, {failed} unconfirmed.')
        # Preserve the original morning deadline schedule. Durable daily claims
        # suppress repeats on subsequent ticks and catch up after a missed tick.
        if timezone.now().astimezone(datetime_timezone.utc).hour >= 14:
            call_command('send_deadline_reminders', stdout=self.stdout)
        if failed:
            raise CommandError('A staff email could not be confirmed. The message and in-app alert remain available.')
