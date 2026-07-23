from django.conf import settings
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
