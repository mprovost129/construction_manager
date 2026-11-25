from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ProjectManagerMigrationTests(TransactionTestCase):
    def test_existing_pm_grants_are_corrected_without_restoring_revoked_access(self):
        before = [("projects", "0054_jobsiteshare")]
        after = [
            ("projects", "0055_projectinternalaccess_projects_pm_has_management_access")
        ]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        try:
            apps = executor.loader.project_state(before).apps
            User = apps.get_model("users", "User")
            Organization = apps.get_model("projects", "Organization")
            Membership = apps.get_model("projects", "OrganizationMembership")
            Project = apps.get_model("projects", "Project")
            Access = apps.get_model("projects", "ProjectInternalAccess")
            org = Organization.objects.create(
                name="Migration company", slug="migration-company"
            )
            project = Project.objects.create(organization=org, name="Existing job")
            entries = []
            for i, (pm, active) in enumerate(
                [(True, True), (True, False), (False, True)]
            ):
                user = User.objects.create(email=f"migration-{i}@example.test")
                membership = Membership.objects.create(
                    organization=org, user=user, role="project_manager"
                )
                entries.append(
                    Access.objects.create(
                        project=project,
                        membership=membership,
                        is_project_manager=pm,
                        is_active=active,
                        receives_notifications=False,
                    )
                )
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            Access = executor.loader.project_state(after).apps.get_model(
                "projects", "ProjectInternalAccess"
            )
            for old in entries:
                row = Access.objects.get(pk=old.pk)
                self.assertEqual(row.can_manage, old.is_project_manager)
                self.assertEqual(row.can_invite_clients, old.is_project_manager)
                self.assertEqual(row.is_active, old.is_active)
                self.assertFalse(row.receives_notifications)
        finally:
            MigrationExecutor(connection).migrate(after)
