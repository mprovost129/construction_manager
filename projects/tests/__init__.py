from projects.models import ProjectInternalAccess


def grant_internal_access(
    user,
    *projects,
    can_manage=True,
    can_invite_clients=True,
    receives_notifications=True,
):
    membership = user.organization_memberships.get(
        organization=projects[0].organization
    )
    for project in projects:
        ProjectInternalAccess.objects.create(
            project=project,
            membership=membership,
            can_manage=can_manage,
            can_invite_clients=can_invite_clients,
            receives_notifications=receives_notifications,
        )
