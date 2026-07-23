from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import OrganizationMembership, ProjectInvitation, ProjectMembership


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
    return invitation.project
