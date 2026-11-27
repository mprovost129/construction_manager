"""Compatibility entry point for existing overdue-only schedules."""
from django.core.management.base import BaseCommand
from django.utils import timezone

from projects.deadlines import due_items, send_deadline
from projects.models import ActivityEvent
from projects.services import record_activity


class Command(BaseCommand):
    help = 'Send overdue selection reminders, at most once per recipient per day.'

    def handle(self, *args, **options):
        today = timezone.localdate()
        count = 0
        for item in due_items(today, overdue_only=True):
            sent = send_deadline(item, today=today)
            if sent:
                record_activity(organization=item.project.organization, project=item.project,
                    event_type=ActivityEvent.Type.SELECTION_REMINDER_SENT,
                    summary=f'Automatic reminder processed for {item.display_number}.',
                    metadata={'selection_id': item.pk, 'automatic': True})
            count += sent
        self.stdout.write(f'Processed {count} overdue selection recipient reminder(s).')
