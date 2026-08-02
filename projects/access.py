from django.db.models import Q

from .models import (
    Organization,
    OrganizationMembership,
    Project,
    ProjectInternalAccess,
)

MANAGEMENT_ROLES = (
    OrganizationMembership.Role.ADMIN,
    OrganizationMembership.Role.MANAGER,
    OrganizationMembership.Role.PROJECT_MANAGER,
)


def projects_for_user(user):
    if not user.is_authenticated:
        return Project.objects.none()
    if user.is_superuser:
        return Project.objects.all()

    admin_organization_ids = user.organization_memberships.filter(
        is_active=True,
        role=OrganizationMembership.Role.ADMIN,
    ).values('organization_id')
    projects = Project.objects.filter(
        Q(organization_id__in=admin_organization_ids)
        | Q(
            internal_access__membership__user=user,
            internal_access__membership__is_active=True,
            internal_access__is_active=True,
        )
        | Q(project_memberships__user=user, project_memberships__is_active=True)
    ).distinct()
    from subscriptions.entitlements import entitled_organization_ids

    allowed_organization_ids = entitled_organization_ids()
    if allowed_organization_ids is not None:
        projects = projects.filter(organization_id__in=allowed_organization_ids)
    return projects


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
    if not membership or not membership.is_internal:
        return False
    if membership.role == OrganizationMembership.Role.ADMIN:
        return True
    return ProjectInternalAccess.objects.filter(
        project=project,
        membership=membership,
        is_active=True,
        can_manage=True,
    ).exists()


def can_invite_clients(user, project):
    if user.is_superuser:
        return True
    membership = organization_membership_for(user, project.organization)
    if not membership or not membership.is_internal:
        return False
    if membership.role == OrganizationMembership.Role.ADMIN:
        return True
    return ProjectInternalAccess.objects.filter(
        project=project,
        membership=membership,
        is_active=True,
    ).filter(Q(can_manage=True) | Q(can_invite_clients=True)).exists()


def can_use_project_messaging(user, project):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    organization_membership = organization_membership_for(
        user, project.organization
    )
    if (
        organization_membership
        and organization_membership.is_internal
        and organization_membership.role != OrganizationMembership.Role.ACCOUNTANT
    ):
        return projects_for_user(user).filter(pk=project.pk).exists()
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


def can_use_estimates(user, project):
    if not user.is_authenticated:
        return False
    if user.is_superuser or is_project_client(user, project):
        return True
    membership = organization_membership_for(user, project.organization)
    return bool(
        membership
        and membership.is_internal
        and membership.can_view_project_financials
        and projects_for_user(user).filter(pk=project.pk).exists()
    )


def can_view_project_budget(user, project):
    """Internal cost, budget, committed/actual cost, and profitability visibility.

    Deliberately narrower than can_view_project_financials (which includes every
    internal role): budget and margin data is restricted to company administrators,
    management roles, and accountants, never clients.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    membership = organization_membership_for(user, project.organization)
    return bool(
        membership
        and membership.role in (*MANAGEMENT_ROLES, OrganizationMembership.Role.ACCOUNTANT)
        and projects_for_user(user).filter(pk=project.pk).exists()
    )


def can_use_schedule(user, project):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    membership = organization_membership_for(user, project.organization)
    return bool(
        membership
        and membership.is_internal
        and membership.role != OrganizationMembership.Role.ACCOUNTANT
        and projects_for_user(user).filter(pk=project.pk).exists()
    )


def can_manage_schedule(user, project):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    membership = organization_membership_for(user, project.organization)
    return bool(
        membership
        and membership.is_internal
        and membership.role in MANAGEMENT_ROLES
        and projects_for_user(user).filter(pk=project.pk).exists()
    )


def can_use_action_center(user, project):
    return can_use_project_messaging(user, project)
