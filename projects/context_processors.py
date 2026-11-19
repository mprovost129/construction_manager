from django.conf import settings

from .access import accounting_organizations_for_user, internal_organizations_for_user
from .selection_templates import template_organizations


def application_identity(request):
    from core.operations import can_view_operations

    return {
        'can_view_operations': not getattr(request, 'client_preview_active', False) and can_view_operations(request.user),
        'app_name': settings.APP_NAME,
        'support_email': settings.SUPPORT_EMAIL,
        'quickbooks_integration_enabled': settings.QUICKBOOKS_INTEGRATION_ENABLED,
        'subscription_billing_enabled': settings.SUBSCRIPTION_BILLING_ENABLED,
    }


def workspace_navigation(request):
    user = request.user
    if not user.is_authenticated:
        return {
            'has_internal_access': False,
            'can_create_projects': False,
            'can_manage_integrations': False,
            'can_manage_templates': False,
            'unread_notification_count': 0,
        }
    has_internal_access = internal_organizations_for_user(user).exists()
    can_create_projects = internal_organizations_for_user(
        user, management_only=True
    ).exists()
    return {
        'has_internal_access': has_internal_access,
        'can_create_projects': can_create_projects,
        'can_manage_templates': template_organizations(user).exists(),
        'can_manage_integrations': accounting_organizations_for_user(user).exists(),
        'unread_notification_count': 0 if getattr(request, 'client_preview_active', False) else user.in_app_notifications.filter(
            read_at__isnull=True
        ).count(),
    }
