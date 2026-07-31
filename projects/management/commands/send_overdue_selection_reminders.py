from django.core.management.base import BaseCommand
from django.utils import timezone

from projects.models import ActivityEvent, FinishSelection
from projects.services import record_activity, send_selection_overdue_reminder


class Command(BaseCommand):
    help = (
        'Email active clients for every open finish selection that is past its due '
        'date. Intended to run once daily from an external scheduler (e.g. a Render '
        'Cron Job); each run only re-sends for selections still overdue.'
    )

    def handle(self, *args, **options):
        today = timezone.localdate()
        overdue_selections = FinishSelection.objects.filter(
            status=FinishSelection.Status.OPEN,
            due_date__lt=today,
        ).select_related('project__organization')

        sent_count = 0
        for selection in overdue_selections:
            delivery_result = send_selection_overdue_reminder(selection)
            if not delivery_result:
                self.stdout.write(
                    self.style.WARNING(
                        f'No reminder sent for {selection.display_number} '
                        f'({selection.project.name}) - no active client recipients.'
                    )
                )
                continue
            record_activity(
                organization=selection.project.organization,
                project=selection.project,
                actor=None,
                event_type=ActivityEvent.Type.SELECTION_REMINDER_SENT,
                summary=(
                    f'Automatic reminder sent for {selection.display_number} - '
                    f'"{selection.title}".'
                ),
                metadata={'selection_id': selection.pk, 'automatic': True},
            )
            sent_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Sent {sent_count} overdue selection reminder(s) of '
                f'{overdue_selections.count()} overdue selection(s).'
            )
        )
