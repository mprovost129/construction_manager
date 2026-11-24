import hashlib
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from core.models import OperationalEvent
from core.operations import record_operational_event

from .models import (
    ActivityEvent,
    DocumentSignatureEvent,
    InAppNotification,
    NotificationPreference,
    OrganizationInvitation,
    OrganizationMembership,
    ProjectCostEntry,
    ProjectInvitation,
    ProjectMembership,
)

logger = logging.getLogger(__name__)

DOCUMENT_SIGNATURE_CONSENT = (
    'I have reviewed the identified document and agree to use an electronic '
    'signature for this transaction. I understand that typing my name and '
    'selecting Sign document is intended to create my legally binding electronic '
    'signature for this exact document version.'
)


def document_version_sha256(version):
    """Return a stable SHA-256 fingerprint for the exact stored file bytes."""
    digest = hashlib.sha256()
    version.file.open('rb')
    try:
        for chunk in iter(lambda: version.file.read(1024 * 1024), b''):
            digest.update(chunk)
    finally:
        version.file.close()
    return digest.hexdigest()


def signature_request_metadata(request):
    """Capture conservative request metadata without trusting forwarded headers."""
    return {
        'ip_address': request.META.get('REMOTE_ADDR') or None,
        'user_agent': request.META.get('HTTP_USER_AGENT', '')[:500],
    }


def record_signature_event(signature_request, event_type, request, *, metadata=None):
    request_metadata = signature_request_metadata(request)
    return DocumentSignatureEvent.objects.create(
        signature_request=signature_request,
        event_type=event_type,
        actor=request.user if request.user.is_authenticated else None,
        ip_address=request_metadata['ip_address'],
        user_agent=request_metadata['user_agent'],
        metadata=metadata or {},
    )


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


def send_notification_email(*, subject, message, recipient_list, html_message=None):
    try:
        sent_count = send_mail(
            subject=subject,
            message=message,
            html_message=html_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipient_list,
        )
    except Exception:
        record_operational_event(OperationalEvent.Kind.EMAIL_FAILED)
        logger.exception('Notification email delivery failed.')
        return None
    if not sent_count:
        record_operational_event(OperationalEvent.Kind.EMAIL_FAILED)
        logger.error('Notification email backend reported no delivered messages.')
        return None
    test_backend = settings.EMAIL_BACKEND in {
        'django.core.mail.backends.console.EmailBackend',
        'django.core.mail.backends.locmem.EmailBackend',
        'django.core.mail.backends.filebased.EmailBackend',
        'django.core.mail.backends.dummy.EmailBackend',
    }
    record_operational_event(
        OperationalEvent.Kind.EMAIL_TEST if test_backend
        else OperationalEvent.Kind.EMAIL_ACCEPTED
    )
    return sent_count


def deliver_project_notification(
    *, request, project, event_type, title, message, url_path, recipient_list, html_message=None
):
    """Deliver a project event through each recipient's selected channels."""
    normalized_emails = {email.strip().casefold() for email in recipient_list if email}
    users = list(
        get_user_model().objects.filter(
            email__in=normalized_emails,
            is_active=True,
        )
    )
    preferences = {
        preference.user_id: preference
        for preference in NotificationPreference.objects.filter(
            project=project,
            user__in=users,
            event_type=event_type,
        )
    }
    email_recipients = []
    in_app_notifications = []
    for user in users:
        preference = preferences.get(user.pk)
        email_enabled = preference.email_enabled if preference else True
        in_app_enabled = preference.in_app_enabled if preference else True
        if email_enabled:
            email_recipients.append(user.email)
        if in_app_enabled:
            in_app_notifications.append(
                InAppNotification(
                    user=user,
                    project=project,
                    event_type=event_type,
                    title=title[:200],
                    message=message,
                    url_path=url_path,
                )
            )
    if in_app_notifications:
        InAppNotification.objects.bulk_create(in_app_notifications)
    if not email_recipients:
        return 0
    absolute_url = (
        request.build_absolute_uri(url_path)
        if request is not None
        else f'{settings.SITE_BASE_URL.rstrip("/")}{url_path}'
    )
    return send_notification_email(
        subject=title,
        message=f'{message}\n\nView in {settings.APP_NAME}: {absolute_url}',
        recipient_list=sorted(set(email_recipients)),
        html_message=(str(html_message) + str(format_html('<p><a href="{}">Open the conversation to respond</a></p>', absolute_url))) if html_message else None,
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
        client_access_type=ProjectMembership.ClientAccessType.PRIMARY,
        is_active=True,
        user__is_active=True,
    ).values_list('user_id', flat=True)
    author_is_client = project.project_memberships.filter(
        role=OrganizationMembership.Role.CLIENT,
        client_access_type=ProjectMembership.ClientAccessType.PRIMARY,
        is_active=True,
        user=author,
    ).exists()
    if author_is_client:
        emails = assigned_project_manager_recipients(
            project,
            exclude_user=author,
        )
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
    thread_path = reverse(
        'projects:message_thread',
        args=(thread.project_id, thread.pk),
    )
    from .message_content import rendered_message
    action = 'New conversation' if new_thread else 'New reply'
    return deliver_project_notification(
        request=request,
        project=thread.project,
        event_type=NotificationPreference.Event.MESSAGES,
        title=f'{action} - {thread.project.name}: {thread.subject}',
        html_message=format_html('<p>{} posted in {} for {}.</p><div>{}</div><p>Shared with the client and assigned project team.</p>', message.author.email, thread.subject, thread.project.name, rendered_message(message)),
        message=(
            f'{message.author.email} posted in "{thread.subject}" for '
            f'{thread.project.name}.\n\n{message.body}'
        ),
        url_path=thread_path,
        recipient_list=recipients,
    )


def document_client_recipients(project):
    emails = get_user_model().objects.filter(
        project_memberships__project=project,
        project_memberships__role=OrganizationMembership.Role.CLIENT,
        project_memberships__client_access_type=ProjectMembership.ClientAccessType.PRIMARY,
        project_memberships__is_active=True,
        is_active=True,
    ).values_list('email', flat=True)
    return sorted(set(emails))


def project_approval_recipients(project):
    emails = get_user_model().objects.filter(
        project_memberships__project=project,
        project_memberships__role=OrganizationMembership.Role.CLIENT,
        project_memberships__client_access_type__in=(
            ProjectMembership.ClientAccessType.PRIMARY,
            ProjectMembership.ClientAccessType.SIGNER,
        ),
        project_memberships__is_active=True,
        is_active=True,
    ).values_list('email', flat=True)
    return sorted(set(emails))


def send_daily_log_published_notification(request, daily_log):
    if not daily_log.client_visible:
        return 0
    recipients = document_client_recipients(daily_log.project)
    if not recipients:
        return 0
    log_path = reverse(
        'projects:daily_log_detail',
        args=(daily_log.project_id, daily_log.pk),
    )
    return deliver_project_notification(
        request=request,
        project=daily_log.project,
        event_type=NotificationPreference.Event.DAILY_LOGS,
        title=f'Project update - {daily_log.project.name}: {daily_log.title}',
        message=(
            f'{daily_log.project.organization.name} posted a new project update '
            f'for {daily_log.project.name}.\n\n'
            f'{daily_log.title}\n{daily_log.summary}'
        ),
        url_path=log_path,
        recipient_list=recipients,
    )


def document_internal_recipients(project, *, exclude_user=None):
    # Company-wide access is not a subscription to every project's activity.
    # An administrator receives these only when explicitly assigned as a PM.
    return assigned_project_manager_recipients(project, exclude_user=exclude_user)


def assigned_project_manager_recipients(project, *, exclude_user=None):
    memberships = project.organization.memberships.filter(
        project_access__project=project,
        project_access__is_active=True,
        project_access__is_project_manager=True,
        is_active=True,
        user__is_active=True,
    )
    if exclude_user:
        memberships = memberships.exclude(user=exclude_user)
    return sorted(set(memberships.values_list('user__email', flat=True)))


def send_project_assignment_notification(request, access):
    project_path = reverse('projects:detail', args=(access.project_id,))
    return deliver_project_notification(
        request=request,
        project=access.project,
        event_type=NotificationPreference.Event.PROJECT_ASSIGNMENTS,
        title=f'Project assignment - {access.project.name}',
        message=(
            f'You were assigned as a project manager for {access.project.name}. '
            'Client messages for this project will be routed to the assigned '
            'project managers.'
        ),
        url_path=project_path,
        recipient_list=[access.membership.user.email],
    )


def send_client_upload_notification(request, version):
    document = version.document
    recipients = document_internal_recipients(
        document.project,
        exclude_user=version.uploaded_by,
    )
    if not recipients:
        return 0
    document_path = reverse(
        'projects:document_detail', args=(document.project_id, document.pk)
    )
    return deliver_project_notification(
        request=request,
        project=document.project,
        event_type=NotificationPreference.Event.DOCUMENTS,
        title=f'Client upload - {document.project.name}: {document.title}',
        message=(
            f'{version.uploaded_by.email} uploaded {version.original_filename} for '
            f'{document.project.name}.'
        ),
        url_path=document_path,
        recipient_list=recipients,
    )


def send_document_available_notification(request, version):
    document = version.document
    if not document.client_visible:
        return 0
    recipients = document_client_recipients(document.project)
    if not recipients:
        return 0
    document_path = reverse(
        'projects:document_detail', args=(document.project_id, document.pk)
    )
    action = 'Review requested' if document.requires_client_approval else 'New document'
    return deliver_project_notification(
        request=request,
        project=document.project,
        event_type=NotificationPreference.Event.DOCUMENTS,
        title=f'{action} - {document.project.name}: {document.title}',
        message=(
            f'{document.title} version {version.version_number} is available for '
            f'{document.project.name}.'
        ),
        url_path=document_path,
        recipient_list=recipients,
    )


def send_document_decision_notification(request, decision):
    document = decision.version.document
    recipients = document_internal_recipients(
        document.project, exclude_user=decision.decided_by
    )
    if not recipients:
        return 0
    document_path = reverse(
        'projects:document_detail', args=(document.project_id, document.pk)
    )
    return deliver_project_notification(
        request=request,
        project=document.project,
        event_type=NotificationPreference.Event.DOCUMENTS,
        title=(
            f'Document {decision.get_decision_display().lower()} - '
            f'{document.project.name}: {document.title}'
        ),
        message=(
            f'{decision.decided_by.email} {decision.get_decision_display().lower()} '
            f'{document.title} version {decision.version.version_number}.\n\n'
            f'Comment: {decision.comment or "No comment provided."}'
        ),
        url_path=document_path,
        recipient_list=recipients,
    )


def send_document_signature_request_notification(request, signature_request):
    document = signature_request.document
    review_path = reverse(
        'projects:document_signature_review',
        args=(document.project_id, document.pk, signature_request.pk),
    )
    due_line = (
        f'Please respond by {signature_request.due_date:%B %d, %Y}.\n\n'
        if signature_request.due_date
        else ''
    )
    message = (
        f'{signature_request.requested_by.email} requested your electronic '
        f'signature on {document.title} version '
        f'{signature_request.version.version_number} for '
        f'{document.project.name}.\n\n'
    )
    if signature_request.message:
        message += f'{signature_request.message}\n\n'
    return deliver_project_notification(
        request=request,
        project=document.project,
        event_type=NotificationPreference.Event.DOCUMENTS,
        title=f'Signature requested - {document.project.name}: {document.title}',
        message=(
            message
            + due_line
            + 'You must sign in with the invited client account before responding.'
        ),
        url_path=review_path,
        recipient_list=[signature_request.signer.email],
    )


def send_document_signature_result_notification(request, signature_request):
    document = signature_request.document
    recipients = document_internal_recipients(
        document.project,
        exclude_user=signature_request.signer,
    )
    recipients = sorted(set(recipients) | {signature_request.signer.email})
    detail_path = reverse(
        'projects:document_detail',
        args=(document.project_id, document.pk),
    )
    return deliver_project_notification(
        request=request,
        project=document.project,
        event_type=NotificationPreference.Event.DOCUMENTS,
        title=(
            f'Document {signature_request.get_status_display().lower()} - '
            f'{document.project.name}: {document.title}'
        ),
        message=(
            f'{signature_request.signer.email} '
            f'{signature_request.get_status_display().lower()} '
            f'{document.title} version '
            f'{signature_request.version.version_number}.'
        ),
        url_path=detail_path,
        recipient_list=recipients,
    )


def send_change_order_review_notification(request, change_order):
    recipients = project_approval_recipients(change_order.project)
    if not recipients:
        return 0
    detail_path = reverse(
        'projects:change_order_detail',
        args=(change_order.project_id, change_order.pk),
    )
    return deliver_project_notification(
        request=request,
        project=change_order.project,
        event_type=NotificationPreference.Event.CHANGE_ORDERS,
        title=(
            f'Change order review requested - {change_order.project.name}: '
            f'{change_order.display_number}'
        ),
        message=(
            f'{change_order.display_number} - {change_order.title} is ready for '
            f'your review for {change_order.project.name}.\n\n'
            f'Client price change: ${change_order.price_delta:,.2f}\n'
            f'Schedule change: {change_order.schedule_delta_days} day(s)'
        ),
        url_path=detail_path,
        recipient_list=recipients,
    )


def send_change_order_decision_notification(request, change_order):
    recipients = document_internal_recipients(
        change_order.project, exclude_user=change_order.decided_by
    )
    if not recipients:
        return 0
    detail_path = reverse(
        'projects:change_order_detail',
        args=(change_order.project_id, change_order.pk),
    )
    return deliver_project_notification(
        request=request,
        project=change_order.project,
        event_type=NotificationPreference.Event.CHANGE_ORDERS,
        title=(
            f'Change order {change_order.get_status_display().lower()} - '
            f'{change_order.project.name}: {change_order.display_number}'
        ),
        message=(
            f'{change_order.decided_by.email} '
            f'{change_order.get_status_display().lower()} '
            f'{change_order.display_number} - {change_order.title}.\n\n'
            f'Comment: {change_order.client_comment or "No comment provided."}'
        ),
        url_path=detail_path,
        recipient_list=recipients,
    )


def send_change_order_voided_notification(request, change_order):
    recipients = project_approval_recipients(change_order.project)
    if not recipients:
        return 0
    detail_path = reverse(
        'projects:change_order_detail',
        args=(change_order.project_id, change_order.pk),
    )
    return deliver_project_notification(
        request=request,
        project=change_order.project,
        event_type=NotificationPreference.Event.CHANGE_ORDERS,
        title=(
            f'Change order withdrawn - {change_order.project.name}: '
            f'{change_order.display_number}'
        ),
        message=(
            f'{change_order.display_number} - {change_order.title} has been '
            'withdrawn and no decision is required.'
        ),
        url_path=detail_path,
        recipient_list=recipients,
    )


def send_estimate_review_notification(request, estimate):
    recipients = project_approval_recipients(estimate.project)
    if not recipients:
        return 0
    detail_path = reverse(
        'projects:estimate_detail',
        args=(estimate.project_id, estimate.pk),
    )
    return deliver_project_notification(
        request=request,
        project=estimate.project,
        event_type=NotificationPreference.Event.ESTIMATES,
        title=(
            f'Estimate review requested - {estimate.project.name}: '
            f'{estimate.display_number}'
        ),
        message=(
            f'{estimate.display_number} - {estimate.title} is ready for '
            f'your review for {estimate.project.name}.\n\n'
            f'Total: ${estimate.price_total:,.2f}'
        ),
        url_path=detail_path,
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
    recipients = project_approval_recipients(estimate.project)
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
    detail_path = reverse(
        'projects:selection_detail',
        args=(selection.project_id, selection.pk),
    )
    due_copy = (
        f'Decision due: {selection.due_date:%B %d, %Y}\n'
        if selection.due_date
        else ''
    )
    return deliver_project_notification(
        request=request,
        project=selection.project,
        event_type=NotificationPreference.Event.SELECTIONS,
        title=(
            f'Finish selection requested - {selection.project.name}: '
            f'{selection.display_number}'
        ),
        message=(
            f'{selection.display_number} - {selection.title} is ready for your '
            f'selection for {selection.project.name}.\n\n'
            f'Allowance: ${selection.allowance_amount:,.2f}\n'
            f'{due_copy}'
        ),
        url_path=detail_path,
        recipient_list=recipients,
    )


def send_selection_chosen_notification(request, selection):
    recipients = document_internal_recipients(
        selection.project, exclude_user=selection.selected_by
    )
    if not recipients:
        return 0
    detail_path = reverse(
        'projects:selection_detail',
        args=(selection.project_id, selection.pk),
    )
    return deliver_project_notification(
        request=request,
        project=selection.project,
        event_type=NotificationPreference.Event.SELECTIONS,
        title=(
            f'Finish selected - {selection.project.name}: '
            f'{selection.display_number}'
        ),
        message=(
            f'{selection.selected_by.email} selected '
            f'{selection.chosen_option.name} for {selection.display_number} - '
            f'{selection.title}.\n\n'
            f'Price: ${selection.chosen_option.price:,.2f} '
            f'({format_allowance_variance(selection.chosen_option)})\n'
            f'Comment: {selection.client_comment or "No comment provided."}'
        ),
        url_path=detail_path,
        recipient_list=recipients,
    )


def send_selection_voided_notification(request, selection):
    recipients = document_client_recipients(selection.project)
    if not recipients:
        return 0
    detail_path = reverse(
        'projects:selection_detail',
        args=(selection.project_id, selection.pk),
    )
    return deliver_project_notification(
        request=request,
        project=selection.project,
        event_type=NotificationPreference.Event.SELECTIONS,
        title=(
            f'Selection withdrawn - {selection.project.name}: '
            f'{selection.display_number}'
        ),
        message=(
            f'{selection.display_number} - {selection.title} has been withdrawn '
            'and no selection is required.'
        ),
        url_path=detail_path,
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
    detail_path = reverse(
        'projects:selection_detail', args=(selection.project_id, selection.pk)
    )
    target_price_copy = (
        f'Target price: ${custom_request.target_price:,.2f}\n'
        if custom_request.target_price is not None
        else ''
    )
    return deliver_project_notification(
        request=request,
        project=selection.project,
        event_type=NotificationPreference.Event.SELECTIONS,
        title=(
            f'Custom option requested - {selection.project.name}: '
            f'{selection.display_number}'
        ),
        message=(
            f'{custom_request.requested_by.email} requested a custom option for '
            f'{selection.display_number} - {selection.title}.\n\n'
            f'{custom_request.description}\n\n'
            f'{target_price_copy}'
        ),
        url_path=detail_path,
        recipient_list=recipients,
    )


def send_selection_overdue_reminder(selection, request=None):
    from .deadlines import send_deadline
    return send_deadline(selection)


def send_schedule_milestone_notification(request, milestone, *, withdrawn=False):
    recipients = document_client_recipients(milestone.project)
    if not recipients:
        return 0
    schedule_path = reverse('projects:schedule', args=(milestone.project_id,))
    if withdrawn:
        subject = f'Schedule updated - {milestone.project.name}'
        body = (
            f'The previously shared milestone "{milestone.title}" is no longer '
            'shown on the client schedule.\n\n'
            'Open the current schedule for details.'
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
            'Open the schedule for details.'
        )
    return deliver_project_notification(
        request=request,
        project=milestone.project,
        event_type=NotificationPreference.Event.SCHEDULE,
        title=subject,
        message=body,
        url_path=schedule_path,
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

    if (
        invitation.role == OrganizationMembership.Role.CLIENT
        and invitation.client_access_type == ProjectMembership.ClientAccessType.PRIMARY
    ):
        existing_client = ProjectMembership.objects.select_for_update().filter(
            project=invitation.project,
            role=OrganizationMembership.Role.CLIENT,
            client_access_type=ProjectMembership.ClientAccessType.PRIMARY,
            is_active=True,
        ).exclude(user=user)
        if existing_client.exists():
            raise ValidationError(
                'This project already has a designated client account holder. '
                'Ask the project administrator to replace that access first.'
            )

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
            'client_access_type': invitation.client_access_type,
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
        summary=(
            f'{user.email} joined the project as '
            f'{invitation.get_client_access_type_display().lower()}.'
        ),
        metadata={
            'email': user.email,
            'client_access_type': invitation.client_access_type,
        },
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

    get_user_model().objects.select_for_update().get(pk=user.pk)
    membership, created = OrganizationMembership.objects.get_or_create(
        organization=invitation.organization,
        user=user,
        defaults={'role': invitation.role, 'mfa_required': invitation.mfa_required, 'accounting_view': invitation.accounting_view,
                  'desktop_manage': invitation.desktop_manage, 'desktop_check': invitation.desktop_check,
                  'accounting_edit': invitation.accounting_edit,
                  'project_budget_view': invitation.project_budget_view},
    )
    if not created:
        if membership.role not in OrganizationMembership.INTERNAL_ROLES:
            raise ValidationError(
                'Your existing company role conflicts with this team invitation.'
            )
        membership.role = invitation.role
        membership.is_active = True
        membership.mfa_required = invitation.mfa_required
        membership.accounting_view = invitation.accounting_view
        membership.accounting_edit = invitation.accounting_edit
        membership.desktop_manage = invitation.desktop_manage
        membership.desktop_check = invitation.desktop_check
        membership.project_budget_view = invitation.project_budget_view
        membership.save(update_fields=('role', 'is_active', 'accounting_view',
                                       'accounting_edit', 'desktop_manage', 'desktop_check', 'project_budget_view', 'mfa_required'))

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
