from .access import internal_organizations_for_user


def workspace_navigation(request):
    user = request.user
    if not user.is_authenticated:
        return {
            'has_internal_access': False,
            'can_create_projects': False,
            'can_manage_integrations': False,
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
    }
