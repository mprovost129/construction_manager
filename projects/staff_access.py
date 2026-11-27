"""Internal conversations require current company, project and participant access."""
from django.contrib.auth import get_user_model
from django.db.models import Q

from .access import internal_organizations_for_user, projects_for_user
from .models import InAppNotification, OrganizationMembership, StaffThread


def staff_projects(user):
    projects = projects_for_user(user).filter(organization__in=internal_organizations_for_user(user))
    if user.is_superuser:
        return projects
    admins = OrganizationMembership.objects.filter(user=user, is_active=True, role='admin').values('organization_id')
    return projects.filter(Q(organization_id__in=admins) | Q(
        internal_access__membership__user=user, internal_access__membership__is_active=True,
        internal_access__membership__role__in=OrganizationMembership.INTERNAL_ROLES,
        internal_access__is_active=True)).distinct()


def staff_recipients(organization, project=None):
    memberships = OrganizationMembership.objects.filter(
        organization=organization, is_active=True, role__in=OrganizationMembership.INTERNAL_ROLES)
    if project:
        memberships = memberships.filter(
            Q(role=OrganizationMembership.Role.ADMIN)
            | Q(project_access__project=project, project_access__is_active=True))
    return get_user_model().objects.filter(is_active=True, pk__in=memberships.values('user_id')).order_by('first_name', 'last_name', 'email')


def staff_threads(user):
    if not user.is_authenticated or not user.is_active or getattr(user, '_client_preview_project_id', None):
        return StaffThread.objects.none()
    return StaffThread.objects.filter(
        organization__in=internal_organizations_for_user(user), participants__user=user,
    ).filter(Q(project__isnull=True) | Q(project__in=staff_projects(user)))


def visible_notifications(user):
    if not user.is_authenticated or not user.is_active or getattr(user, '_client_preview_project_id', None):
        return InAppNotification.objects.none()
    return user.in_app_notifications.filter(
        Q(staff_thread__isnull=True) | Q(staff_thread__in=staff_threads(user))).filter(
        Q(project__isnull=True) | Q(project__deleted_at__isnull=True))
