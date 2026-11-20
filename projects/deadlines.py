"""Daily deadline notifications, with durable claims across concurrent runs."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .access import projects_for_user
from .models import (
    DeadlineReminder,
    FinishSelection,
    InAppNotification,
    NotificationPreference,
    Project,
    PunchItem,
)
from .services import document_client_recipients, send_notification_email


def scheduled_today(due_date, today):
    if not due_date:
        return False
    days = (due_date - today).days
    return days in (3, 1, 0) or (days < 0 and (-days - 1) % 7 == 0)


def recipients_for(item):
    if isinstance(item, FinishSelection):
        return get_user_model().objects.filter(
            is_active=True, email__in=document_client_recipients(item.project))
    from .punch import staff_for
    return staff_for(item.project).filter(pk=item.assigned_to_id)


def send_deadline(item, *, today=None, dry_run=False):
    today = today or timezone.localdate()
    selection = isinstance(item, FinishSelection)
    if item.project.status not in (Project.Status.PLANNING, Project.Status.ACTIVE):
        return 0
    if item.status not in (('open',) if selection else ('open', 'in_progress')):
        return 0
    target = {'selection': item} if selection else {'punch_item': item}
    event = NotificationPreference.Event.SELECTIONS if selection else NotificationPreference.Event.PUNCH_LIST
    url = reverse('projects:selection_detail' if selection else 'projects:punch_detail', args=(item.project_id, item.pk))
    date_copy = 'needs your attention'
    if item.due_date:
        delta = (item.due_date - today).days
        date_copy = 'is overdue' if delta < 0 else ('is due today' if delta == 0 else 'is due soon')
    title = f'Reminder: {"selection" if selection else "punch-list item"} {date_copy} - {item.project.name}'
    message = f'{item.display_number + " - " if selection else ""}{item.title} {date_copy}.'
    if item.due_date:
        message += f' Due date: {item.due_date:%B %d, %Y}.'
    count = 0
    for user in recipients_for(item):
        if not projects_for_user(user).filter(pk=item.project_id).exists():
            continue
        preference = NotificationPreference.objects.filter(user=user, project=item.project, event_type=event).first()
        email_enabled = preference.email_enabled if preference else True
        in_app_enabled = preference.in_app_enabled if preference else True
        if not (email_enabled or in_app_enabled):
            continue
        if dry_run:
            count += not DeadlineReminder.objects.filter(**target, recipient=user, reminder_date=today).exists()
            continue
        # Claim and in-app alert commit together. A unique constraint arbitrates
        # concurrent cron/manual runs; never hold a DB lock during mail delivery.
        with transaction.atomic():
            receipt, created = DeadlineReminder.objects.get_or_create(
                **target, recipient=user, reminder_date=today,
                defaults={'email_status': 'pending' if email_enabled else 'disabled'})
            if not created:
                continue
            if in_app_enabled:
                InAppNotification.objects.create(user=user, project=item.project, event_type=event,
                                                title=title[:200], message=message, url_path=url)
        if email_enabled:
            accepted = send_notification_email(subject=title,
                message=f'{message}\n\nView in {settings.APP_NAME}: {settings.SITE_BASE_URL.rstrip("/")}{url}',
                recipient_list=[user.email])
            receipt.email_status = 'accepted' if accepted else 'failed'
            receipt.save(update_fields=('email_status',))
        count += 1
    return count


def due_items(today, *, overdue_only=False):
    for model, statuses in ((FinishSelection, ('open',)), (PunchItem, ('open', 'in_progress'))):
        if overdue_only and model is PunchItem:
            continue
        items = model.objects.filter(status__in=statuses, due_date__isnull=False, deadline_reminders_enabled=True,
            project__status__in=(Project.Status.PLANNING, Project.Status.ACTIVE)).select_related('project__organization')
        for item in items.iterator(chunk_size=100):
            if (overdue_only and item.due_date < today) or (not overdue_only and scheduled_today(item.due_date, today)):
                yield item
