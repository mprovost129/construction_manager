from .access import internal_organizations_for_user


def workspace_navigation(request):
    user = request.user
    if not user.is_authenticated:
        return {'has_internal_access': False, 'can_create_projects': False}
    has_internal_access = internal_organizations_for_user(user).exists()
    can_create_projects = internal_organizations_for_user(
        user, management_only=True
    ).exists()
    return {
        'has_internal_access': has_internal_access,
        'can_create_projects': can_create_projects,
    }
