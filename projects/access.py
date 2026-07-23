from django.db.models import Q

from .models import OrganizationMembership, Project


def projects_for_user(user):
    if not user.is_authenticated:
        return Project.objects.none()
    if user.is_superuser:
        return Project.objects.all()

    internal_organization_ids = user.organization_memberships.filter(
        is_active=True,
        role__in=OrganizationMembership.INTERNAL_ROLES,
    ).values('organization_id')
    return Project.objects.filter(
        Q(organization_id__in=internal_organization_ids)
        | Q(project_memberships__user=user, project_memberships__is_active=True)
    ).distinct()


def organization_membership_for(user, organization):
    if not user.is_authenticated:
        return None
    return user.organization_memberships.filter(
        organization=organization,
        is_active=True,
    ).first()


def can_invite_clients(user, project):
    if user.is_superuser:
        return True
    membership = organization_membership_for(user, project.organization)
    return bool(membership and membership.can_invite_clients)
