import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import (
    ActivityEvent,
    OrganizationInvitation,
    OrganizationMembership,
    ProjectCostEntry,
    ProjectInvitation,
    ProjectMembership,
)

logger = logging.getLogger(__name__)


def evaluate_approval_progress(decisions, required_approvals):
    """Resolve an outcome from a set of per-user 'approved'/'declined' votes.

    Works for both DocumentDecision and ChangeOrderDecision querysets/lists,
    since both use the same 'approved'/'declined' string values. Any decline
    resolves the outcome immediately; approvals resolve once distinct
    approving users reach the required threshold.
    """
    decisions = list(decisions)
    declined = any(decision.decision == 'declined' for decision in decisions)
    approved_count = len(
        {
            decision.decided_by_id
            for decision in decisions
            if decision.decision == 'approved'
        }
    )
    if declined:
        outcome = 'declined'
        resolved = True
    elif approved_count >= required_approvals:
        outcome = 'approved'
        resolved = True
    else:
        outcome = None
        resolved = False
    return {
        'outcome': outcome,
        'resolved': resolved,
        'approved_count': approved_count,
        'required_approvals': required_approvals,
    }


def send_notification_email(*, subject, message, recipient_list):
    try:
        sent_count = send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipient_list,
        )
    except Exception:
        logger.exception('Notification email delivery failed.')
        return None
    if not sent_count:
        logger.error('Notification email backend reported no delivered messages.')
        return None
    return sent_count


def record_activity(
    *, organization, event_type, summary, actor=None, project=None, metadata=None
):
    return ActivityEvent.objects.create(
        organization=organization,
        project=project,
        actor=actor,
        event_type=event_type,
        summary=summary,
        metadata=metadata or {},
    )


def send_project_invitation(request, invitation):
    accept_url = request.build_absolute_uri(
        reverse('projects:accept_invitation', args=(invitation.token,))
    )
    return send_notification_email(
        subject=f'You are invited to {invitation.project.name}',
        message=(
            f'You have been invited to access {invitation.project.name} in the '
            f'{invitation.project.organization.name} customer portal.\n\n'
            f'Accept your invitation: {accept_url}\n\n'
            'This invitation expires in 7 days.'
        ),
        recipient_list=[invitation.email],
    )


def send_team_invitation(request, invitation):
    accept_url = request.build_absolute_uri(
        reverse('projects:accept_team_invitation', args=(invitation.token,))
    )
    return send_notification_email(
        subject=f'Join {invitation.organization.name}',
        message=(
            f'You have been invited to join {invitation.organization.name} as '
            f'{invitation.get_role_display()}.\n\n'
            f'Accept your invitation: {accept_url}\n\n'
            'This invitation expires in 7 days.'
        ),
        recipient_list=[invitation.email],
    )


def message_notification_recipients(project, author):
    client_user_ids = project.project_memberships.filter(
        role=OrganizationMembership.Role.CLIENT,
        is_active=True,
        user__is_active=True,
    ).values_list('user_id', flat=True)
    author_is_client = project.project_memberships.filter(
        role=OrganizationMembership.Role.CLIENT,
        is_active=True,
        user=author,
    ).exists()
    if author_is_client:
        emails = document_internal_recipients(project, exclude_user=author)
    else:
        emails = get_user_model().objects.filter(
            pk__in=client_user_ids,
            is_active=True,
        ).exclude(pk=author.pk).values_list('email', flat=True)
    return sorted(set(emails))


def send_message_notifications(request, message, *, new_thread=False):
    thread = message.thread
    recipients = message_notification_recipients(thread.project, message.author)
    if not recipients:
        return 0
    thread_url = request.build_absolute_uri(
        reverse(
            'projects:message_thread',
            args=(thread.project_id, thread.pk),
        )
    )
    action = 'New conversation' if new_thread else 'New reply'
    return send_notification_email(
        subject=f'{action} - {thread.project.name}: {thread.subject}',
        message=(
            f'{message.author.email} posted in "{thread.subject}" for '
            f'{thread.project.name}.\n\n{message.body}\n\n'
            f'View and reply: {thread_url}'
        ),
        recipient_list=recipients,
    )


def document_client_recipients(project):
    emails = get_user_model().objects.filter(
        project_memberships__project=project,
        project_memberships__role=OrganizationMembership.Role.CLIENT,
        project_memberships__is_active=True,
        is_active=True,
    ).values_list('email', flat=True)
    return sorted(set(emails))


def document_internal_recipients(project, *, exclude_user=None):
    admin_memberships = project.organization.memberships.filter(
        role=OrganizationMembership.Role.ADMIN,
        is_active=True,
        user__is_active=True,
    )
    assigned_memberships = project.organization.memberships.filter(
        project_access__project=project,
        project_access__is_active=True,
        project_access__receives_notifications=True,
        is_active=True,
        user__is_active=True,
    ).exclude(role=OrganizationMembership.Role.ACCOUNTANT)
    if exclude_user:
        admin_memberships = admin_memberships.exclude(user=exclude_user)
        assigned_memberships = assigned_memberships.exclude(user=exclude_user)
    return sorted(
        set(admin_memberships.values_list('user__email', flat=True))
        | set(assigned_memberships.values_list('user__email', flat=True))
    )


def send_client_upload_notification(request, version):
    document = version.document
    recipients = document_internal_recipients(
        document.project,
        exclude_user=version.uploaded_by,
    )
    if not recipients:
        return 0
    document_url = request.build_absolute_uri(
        reverse('projects:document_detail', args=(document.project_id, document.pk))
    )
    return send_notification_email(
        subject=f'Client upload - {document.project.name}: {document.title}',
        message=(
            f'{version.uploaded_by.email} uploaded {version.original_filename} for '
            f'{document.project.name}.\n\nView upload: {document_url}'
        ),
        recipient_list=recipients,
    )


def send_document_available_notification(request, version):
    document = version.document
    if not document.client_visible:
        return 0
    recipients = document_client_recipients(document.project)
    if not recipients:
        return 0
    document_url = request.build_absolute_uri(
        reverse('projects:document_detail', args=(document.project_id, document.pk))
    )
    action = 'Review requested' if document.requires_client_approval else 'New document'
    return send_notification_email(
        subject=f'{action} - {document.project.name}: {document.title}',
        message=(
            f'{document.title} version {version.version_number} is available for '
            f'{document.project.name}.\n\nView document: {document_url}'
        ),
        recipient_list=recipients,
    )


def send_document_decision_notification(request, decision):
    document = decision.version.document
    recipients = document_internal_recipients(
        document.project, exclude_user=decision.decided_by
    )
    if not recipients:
        return 0
    document_url = request.build_absolute_uri(
        reverse('projects:document_detail', args=(document.project_id, document.pk))
    )
    return send_notification_email(
        subject=(
            f'Document {decision.get_decision_display().lower()} - '
            f'{document.project.name}: {document.title}'
        ),
        message=(
            f'{decision.decided_by.email} {decision.get_decision_display().lower()} '
            f'{document.title} version {decision.version.version_number}.\n\n'
            f'Comment: {decision.comment or "No comment provided."}\n\n'
            f'View document: {document_url}'
        ),
        recipient_list=recipients,
    )


def send_change_order_review_notification(request, change_order):
    recipients = document_client_recipients(change_order.project)
    if not recipients:
        return 0
    detail_url = request.build_absolute_uri(
        reverse(
            'projects:change_order_detail',
            args=(change_order.project_id, change_order.pk),
        )
    )
    return send_notification_email(
        subject=(
            f'Change order review requested - {change_order.project.name}: '
            f'{change_order.display_number}'
        ),
        message=(
            f'{change_order.display_number} - {change_order.title} is ready for '
            f'your review for {change_order.project.name}.\n\n'
            f'Client price change: ${change_order.price_delta:,.2f}\n'
            f'Schedule change: {change_order.schedule_delta_days} day(s)\n\n'
            f'Review and decide: {detail_url}'
        ),
        recipient_list=recipients,
    )


def send_change_order_decision_notification(request, change_order):
    recipients = document_internal_recipients(
        change_order.project, exclude_user=change_order.decided_by
    )
    if not recipients:
        return 0
    detail_url = request.build_absolute_uri(
        reverse(
            'projects:change_order_detail',
            args=(change_order.project_id, change_order.pk),
        )
    )
    return send_notification_email(
        subject=(
            f'Change order {change_order.get_status_display().lower()} - '
            f'{change_order.project.name}: {change_order.display_number}'
        ),
        message=(
            f'{change_order.decided_by.email} '
            f'{change_order.get_status_display().lower()} '
            f'{change_order.display_number} - {change_order.title}.\n\n'
            f'Comment: {change_order.client_comment or "No comment provided."}\n\n'
            f'View change order: {detail_url}'
        ),
        recipient_list=recipients,
    )


def send_change_order_voided_notification(request, change_order):
    recipients = document_client_recipients(change_order.project)
    if not recipients:
        return 0
    detail_url = request.build_absolute_uri(
        reverse(
            'projects:change_order_detail',
            args=(change_order.project_id, change_order.pk),
        )
    )
    return send_notification_email(
        subject=(
            f'Change order withdrawn - {change_order.project.name}: '
            f'{change_order.display_number}'
        ),
        message=(
            f'{change_order.display_number} - {change_order.title} has been '
            f'withdrawn and no decision is required.\n\nView: {detail_url}'
        ),
        recipient_list=recipients,
    )


def send_estimate_review_notification(request, estimate):
    recipients = document_client_recipients(estimate.project)
    if not recipients:
        return 0
    detail_url = request.build_absolute_uri(
        reverse(
            'projects:estimate_detail',
            args=(estimate.project_id, estimate.pk),
        )
    )
    return send_notification_email(
        subject=(
            f'Estimate review requested - {estimate.project.name}: '
            f'{estimate.display_number}'
        ),
        message=(
            f'{estimate.display_number} - {estimate.title} is ready for '
            f'your review for {estimate.project.name}.\n\n'
            f'Total: ${estimate.price_total:,.2f}\n\n'
            f'Review and decide: {detail_url}'
        ),
        recipient_list=recipients,
    )


def send_estimate_decision_notification(request, estimate):
    recipients = document_internal_recipients(
        estimate.project, exclude_user=estimate.decided_by
    )
    if not recipients:
        return 0
    detail_url = request.build_absolute_uri(
        reverse(
            'projects:estimate_detail',
            args=(estimate.project_id, estimate.pk),
        )
    )
    return send_notification_email(
        subject=(
            f'Estimate {estimate.get_status_display().lower()} - '
            f'{estimate.project.name}: {estimate.display_number}'
        ),
        message=(
            f'{estimate.decided_by.email} '
            f'{estimate.get_status_display().lower()} '
            f'{estimate.display_number} - {estimate.title}.\n\n'
            f'Comment: {estimate.client_comment or "No comment provided."}\n\n'
            f'View estimate: {detail_url}'
        ),
        recipient_list=recipients,
    )


def send_estimate_voided_notification(request, estimate):
    recipients = document_client_recipients(estimate.project)
    if not recipients:
        return 0
    detail_url = request.build_absolute_uri(
        reverse(
            'projects:estimate_detail',
            args=(estimate.project_id, estimate.pk),
        )
    )
    return send_notification_email(
        subject=(
            f'Estimate withdrawn - {estimate.project.name}: '
            f'{estimate.display_number}'
        ),
        message=(
            f'{estimate.display_number} - {estimate.title} has been '
            f'withdrawn and no decision is required.\n\nView: {detail_url}'
        ),
        recipient_list=recipients,
    )


def format_allowance_variance(option):
    variance = option.allowance_variance
    if variance > 0:
        return f'${variance:,.2f} over allowance'
    if variance < 0:
        return f'${abs(variance):,.2f} under allowance'
    return 'Within allowance'


def send_selection_review_notification(request, selection):
    recipients = document_client_recipients(selection.project)
    if not recipients:
        return 0
    detail_url = request.build_absolute_uri(
        reverse(
            'projects:selection_detail',
            args=(selection.project_id, selection.pk),
        )
    )
    due_copy = (
        f'Decision due: {selection.due_date:%B %d, %Y}\n'
        if selection.due_date
        else ''
    )
    return send_notification_email(
        subject=(
            f'Finish selection requested - {selection.project.name}: '
            f'{selection.display_number}'
        ),
        message=(
            f'{selection.display_number} - {selection.title} is ready for your '
            f'selection for {selection.project.name}.\n\n'
            f'Allowance: ${selection.allowance_amount:,.2f}\n'
            f'{due_copy}\nReview options and choose: {detail_url}'
        ),
        recipient_list=recipients,
    )


def send_selection_chosen_notification(request, selection):
    recipients = document_internal_recipients(
        selection.project, exclude_user=selection.selected_by
    )
    if not recipients:
        return 0
    detail_url = request.build_absolute_uri(
        reverse(
            'projects:selection_detail',
            args=(selection.project_id, selection.pk),
        )
    )
    return send_notification_email(
        subject=(
            f'Finish selected - {selection.project.name}: '
            f'{selection.display_number}'
        ),
        message=(
            f'{selection.selected_by.email} selected '
            f'{selection.chosen_option.name} for {selection.display_number} - '
            f'{selection.title}.\n\n'
            f'Price: ${selection.chosen_option.price:,.2f} '
            f'({format_allowance_variance(selection.chosen_option)})\n'
            f'Comment: {selection.client_comment or "No comment provided."}\n\n'
            f'View selection: {detail_url}'
        ),
        recipient_list=recipients,
    )


def send_selection_voided_notification(request, selection):
    recipients = document_client_recipients(selection.project)
    if not recipients:
        return 0
    detail_url = request.build_absolute_uri(
        reverse(
            'projects:selection_detail',
            args=(selection.project_id, selection.pk),
        )
    )
    return send_notification_email(
        subject=(
            f'Selection withdrawn - {selection.project.name}: '
            f'{selection.display_number}'
        ),
        message=(
            f'{selection.display_number} - {selection.title} has been withdrawn '
            f'and no selection is required.\n\nView: {detail_url}'
        ),
        recipient_list=recipients,
    )


def selection_detail_url(selection, request=None):
    path = reverse(
        'projects:selection_detail',
        args=(selection.project_id, selection.pk),
    )
    if request is not None:
        return request.build_absolute_uri(path)
    return f'{settings.SITE_BASE_URL.rstrip("/")}{path}'


def send_selection_custom_request_notification(request, custom_request):
    selection = custom_request.selection
    recipients = document_internal_recipients(
        selection.project, exclude_user=custom_request.requested_by
    )
    if not recipients:
        return 0
    detail_url = selection_detail_url(selection, request)
    target_price_copy = (
        f'Target price: ${custom_request.target_price:,.2f}\n'
        if custom_request.target_price is not None
        else ''
    )
    return send_notification_email(
        subject=(
            f'Custom option requested - {selection.project.name}: '
            f'{selection.display_number}'
        ),
        message=(
            f'{custom_request.requested_by.email} requested a custom option for '
            f'{selection.display_number} - {selection.title}.\n\n'
            f'{custom_request.description}\n\n'
            f'{target_price_copy}\nReview and respond: {detail_url}'
        ),
        recipient_list=recipients,
    )


def send_selection_overdue_reminder(selection, request=None):
    recipients = document_client_recipients(selection.project)
    if not recipients:
        return 0
    detail_url = selection_detail_url(selection, request)
    return send_notification_email(
        subject=(
            f'Reminder: finish selection overdue - {selection.project.name}: '
            f'{selection.display_number}'
        ),
        message=(
            f'{selection.display_number} - {selection.title} was due on '
            f'{selection.due_date:%B %d, %Y} and still needs your selection for '
            f'{selection.project.name}.\n\nReview options and choose: {detail_url}'
        ),
        recipient_list=recipients,
    )


def send_schedule_milestone_notification(request, milestone, *, withdrawn=False):
    recipients = document_client_recipients(milestone.project)
    if not recipients:
        return 0
    schedule_url = request.build_absolute_uri(
        reverse('projects:schedule', args=(milestone.project_id,))
    )
    if withdrawn:
        subject = f'Schedule updated - {milestone.project.name}'
        body = (
            f'The previously shared milestone "{milestone.title}" is no longer '
            'shown on the client schedule.\n\n'
            f'View the current schedule: {schedule_url}'
        )
    else:
        date_copy = f'Starts: {milestone.start_date:%B %d, %Y}'
        if milestone.end_date:
            date_copy += f'\nEnds: {milestone.end_date:%B %d, %Y}'
        subject = f'Schedule update - {milestone.project.name}: {milestone.title}'
        body = (
            f'{milestone.title} was added or updated on the client schedule for '
            f'{milestone.project.name}.\n\n'
            f'{date_copy}\nStatus: {milestone.get_status_display()}\n\n'
            f'View the schedule: {schedule_url}'
        )
    return send_notification_email(
        subject=subject,
        message=body,
        recipient_list=recipients,
    )


@transaction.atomic
def accept_project_invitation(invitation, user):
    invitation = ProjectInvitation.objects.select_for_update().select_related(
        'project__organization'
    ).get(pk=invitation.pk)

    if not invitation.is_valid:
        raise ValidationError('This invitation is no longer valid.')
    if invitation.email.casefold() != user.email.casefold():
        raise PermissionDenied('This invitation was sent to another email address.')

    organization = invitation.project.organization
    membership, created = OrganizationMembership.objects.get_or_create(
        organization=organization,
        user=user,
        defaults={'role': invitation.role},
    )
    if not created and membership.role != invitation.role:
        raise ValidationError(
            'Your existing company role conflicts with this invitation. '
            'Ask an administrator to grant project access.'
        )

    ProjectMembership.objects.update_or_create(
        project=invitation.project,
        user=user,
        defaults={
            'role': invitation.role,
            'is_active': True,
            'invited_by': invitation.invited_by,
        },
    )
    invitation.accepted_by = user
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=('accepted_by', 'accepted_at'))
    record_activity(
        organization=organization,
        project=invitation.project,
        actor=user,
        event_type=ActivityEvent.Type.CLIENT_JOINED,
        summary=f'{user.email} joined the project as a client.',
        metadata={'email': user.email},
    )
    return invitation.project


@transaction.atomic
def accept_organization_invitation(invitation, user):
    invitation = OrganizationInvitation.objects.select_for_update().select_related(
        'organization'
    ).get(pk=invitation.pk)
    if not invitation.is_valid:
        raise ValidationError('This invitation is no longer valid.')
    if invitation.email.casefold() != user.email.casefold():
        raise PermissionDenied('This invitation was sent to another email address.')

    membership, created = OrganizationMembership.objects.get_or_create(
        organization=invitation.organization,
        user=user,
        defaults={'role': invitation.role},
    )
    if not created:
        if membership.role not in OrganizationMembership.INTERNAL_ROLES:
            raise ValidationError(
                'Your existing company role conflicts with this team invitation.'
            )
        membership.role = invitation.role
        membership.is_active = True
        membership.save(update_fields=('role', 'is_active'))

    invitation.accepted_by = user
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=('accepted_by', 'accepted_at'))
    record_activity(
        organization=invitation.organization,
        actor=user,
        event_type=ActivityEvent.Type.TEAM_JOINED,
        summary=(
            f'{user.email} joined the company as '
            f'{membership.get_role_display()}.'
        ),
        metadata={'email': user.email, 'role': membership.role},
    )
    return invitation.organization


@transaction.atomic
def record_cost_entry(*, project, actor, category, cost_code, description, amount, incurred_date, note):
    entry = ProjectCostEntry(
        project=project,
        category=category,
        cost_code=cost_code,
        description=description,
        amount=amount,
        incurred_date=incurred_date,
        note=note,
        recorded_by=actor,
    )
    entry.full_clean()
    entry.save()
    record_activity(
        organization=project.organization,
        project=project,
        actor=actor,
        event_type=ActivityEvent.Type.PROJECT_COST_ENTRY_RECORDED,
        summary=f'{actor.email} recorded a ${entry.amount:,.2f} actual cost: {entry.description}.',
        metadata={'cost_entry_id': entry.pk, 'amount': str(entry.amount)},
    )
    return entry


@transaction.atomic
def delete_cost_entry(*, entry_id, actor):
    entry = ProjectCostEntry.objects.select_related('project').get(pk=entry_id)
    project = entry.project
    description = entry.description
    amount = entry.amount
    entry.delete()
    record_activity(
        organization=project.organization,
        project=project,
        actor=actor,
        event_type=ActivityEvent.Type.PROJECT_COST_ENTRY_DELETED,
        summary=f'{actor.email} removed a ${amount:,.2f} actual cost: {description}.',
        metadata={'amount': str(amount)},
    )
    return project
