from django.db.models import Q

from .models import (
    Organization,
    OrganizationMembership,
    Project,
)

MANAGEMENT_ROLES = (
    OrganizationMembership.Role.ADMIN,
    OrganizationMembership.Role.STAFF,
)


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


def internal_organizations_for_user(user, *, management_only=False):
    if not user.is_authenticated:
        return Organization.objects.none()
    if user.is_superuser:
        return Organization.objects.all()
    roles = MANAGEMENT_ROLES if management_only else OrganizationMembership.INTERNAL_ROLES
    return Organization.objects.filter(
        memberships__user=user,
        memberships__is_active=True,
        memberships__role__in=roles,
    ).distinct()


def can_manage_organization(user, organization):
    if user.is_superuser:
        return True
    membership = organization_membership_for(user, organization)
    return bool(
        membership and membership.role == OrganizationMembership.Role.ADMIN
    )


def can_manage_project(user, project):
    if user.is_superuser:
        return True
    membership = organization_membership_for(user, project.organization)
    return bool(membership and membership.role in MANAGEMENT_ROLES)


def can_invite_clients(user, project):
    return can_manage_project(user, project)


def can_use_project_messaging(user, project):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    organization_membership = organization_membership_for(
        user, project.organization
    )
    if organization_membership and organization_membership.role in MANAGEMENT_ROLES:
        return True
    return project.project_memberships.filter(
        user=user,
        is_active=True,
        role=OrganizationMembership.Role.CLIENT,
    ).exists()


def is_project_client(user, project):
    if not user.is_authenticated:
        return False
    return project.project_memberships.filter(
        user=user,
        is_active=True,
        role=OrganizationMembership.Role.CLIENT,
    ).exists()


def can_use_project_documents(user, project):
    return can_use_project_messaging(user, project)


def can_use_change_orders(user, project):
    return can_use_project_messaging(user, project)


def can_use_selections(user, project):
    return can_use_project_messaging(user, project)


def can_use_schedule(user, project):
    return can_use_project_messaging(user, project)


def can_use_action_center(user, project):
    return can_use_project_messaging(user, project)
