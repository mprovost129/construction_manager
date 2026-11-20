from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from projects.deadlines import due_items, send_deadline
from projects.models import DeadlineReminder


class Command(BaseCommand):
    help = 'Send selection/client and punch-list/assignee reminders: 3 days, 1 day, due day, then 1 day overdue and weekly.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Count eligible recipients without sending or writing anything.')

    def handle(self, *args, **options):
        started = timezone.now()
        today = timezone.localdate()
        count = sum(send_deadline(item, today=today, dry_run=options['dry_run']) for item in due_items(today))
        self.stdout.write(f'{"Eligible" if options["dry_run"] else "Processed"} recipient reminders: {count}.')
        if not options['dry_run'] and DeadlineReminder.objects.filter(
            created_at__gte=started, email_status='failed'
        ).exists():
            raise CommandError('Some reminder emails were not confirmed. Check email delivery in Operations; in-app alerts are preserved.')
