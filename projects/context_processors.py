from django.conf import settings

from .access import internal_organizations_for_user


def application_identity(request):
    return {
        'app_name': settings.APP_NAME,
        'support_email': settings.SUPPORT_EMAIL,
    }


def workspace_navigation(request):
    user = request.user
    if not user.is_authenticated:
        return {
            'has_internal_access': False,
            'can_create_projects': False,
            'can_manage_integrations': False,
            'unread_notification_count': 0,
        }
    has_internal_access = internal_organizations_for_user(user).exists()
    can_create_projects = internal_organizations_for_user(
        user, management_only=True
    ).exists()
    return {
        'has_internal_access': has_internal_access,
        'can_create_projects': can_create_projects,
        'can_manage_integrations': (
            user.is_superuser
            or user.organization_memberships.filter(
                is_active=True,
                role='admin',
            ).exists()
        ),
        'unread_notification_count': user.in_app_notifications.filter(
            read_at__isnull=True
        ).count(),
    }
