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
    ProjectInvitation,
    ProjectMembership,
)


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
    send_mail(
        subject=f'You are invited to {invitation.project.name}',
        message=(
            f'You have been invited to access {invitation.project.name} in the '
            f'{invitation.project.organization.name} customer portal.\n\n'
            f'Accept your invitation: {accept_url}\n\n'
            'This invitation expires in 7 days.'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[invitation.email],
    )


def send_team_invitation(request, invitation):
    accept_url = request.build_absolute_uri(
        reverse('projects:accept_team_invitation', args=(invitation.token,))
    )
    send_mail(
        subject=f'Join {invitation.organization.name}',
        message=(
            f'You have been invited to join {invitation.organization.name} as '
            f'{invitation.get_role_display()}.\n\n'
            f'Accept your invitation: {accept_url}\n\n'
            'This invitation expires in 7 days.'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
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
        emails = project.organization.memberships.filter(
            role__in=(
                OrganizationMembership.Role.ADMIN,
                OrganizationMembership.Role.STAFF,
            ),
            is_active=True,
            user__is_active=True,
        ).exclude(user=author).values_list('user__email', flat=True)
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
    return send_mail(
        subject=f'{action} - {thread.project.name}: {thread.subject}',
        message=(
            f'{message.author.email} posted in "{thread.subject}" for '
            f'{thread.project.name}.\n\n{message.body}\n\n'
            f'View and reply: {thread_url}'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
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
    memberships = project.organization.memberships.filter(
        role__in=(
            OrganizationMembership.Role.ADMIN,
            OrganizationMembership.Role.STAFF,
        ),
        is_active=True,
        user__is_active=True,
    )
    if exclude_user:
        memberships = memberships.exclude(user=exclude_user)
    return sorted(set(memberships.values_list('user__email', flat=True)))


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
    return send_mail(
        subject=f'{action} - {document.project.name}: {document.title}',
        message=(
            f'{document.title} version {version.version_number} is available for '
            f'{document.project.name}.\n\nView document: {document_url}'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
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
    return send_mail(
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
        from_email=settings.DEFAULT_FROM_EMAIL,
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
    return send_mail(
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
        from_email=settings.DEFAULT_FROM_EMAIL,
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
    return send_mail(
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
        from_email=settings.DEFAULT_FROM_EMAIL,
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
    return send_mail(
        subject=(
            f'Change order withdrawn - {change_order.project.name}: '
            f'{change_order.display_number}'
        ),
        message=(
            f'{change_order.display_number} - {change_order.title} has been '
            f'withdrawn and no decision is required.\n\nView: {detail_url}'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
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
    return send_mail(
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
        from_email=settings.DEFAULT_FROM_EMAIL,
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
    return send_mail(
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
        from_email=settings.DEFAULT_FROM_EMAIL,
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
    return send_mail(
        subject=(
            f'Selection withdrawn - {selection.project.name}: '
            f'{selection.display_number}'
        ),
        message=(
            f'{selection.display_number} - {selection.title} has been withdrawn '
            f'and no selection is required.\n\nView: {detail_url}'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
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
