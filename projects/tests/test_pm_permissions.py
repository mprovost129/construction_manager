from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from projects import access
from projects.client_preview import SESSION_KEY
from projects.forms import ProjectInternalAccessForm
from projects.models import OrganizationMembership, Project, ProjectInternalAccess
from projects.project_lifecycle import can_copy_project, copy_setup
from projects.tests import test_reconciliation


class ProjectManagerPermissionsTests(TestCase):
    def setUp(self):
        test_reconciliation.ReconciliationTests.setUp(self)
        self.pm = get_user_model().objects.create_user("pm@example.test", "password")
        self.membership = OrganizationMembership.objects.create(
            organization=self.org, user=self.pm, role="project_manager"
        )
        self.url = reverse("projects:internal_access_update", args=[self.project.pk])

    def assign(self):
        return ProjectInternalAccess.objects.create(
            project=self.project, membership=self.membership, is_project_manager=True
        )

    def test_pm_checkbox_alone_grants_workflows_and_client_management(self):
        form = ProjectInternalAccessForm(
            {"membership": self.membership.pk, "is_project_manager": "on"},
            project=self.project,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data["can_manage"])
        self.assertTrue(form.cleaned_data["can_invite_clients"])
        self.client.force_login(self.admin)
        with patch("projects.views.send_project_assignment_notification"):
            self.assertEqual(
                self.client.post(
                    self.url,
                    {"membership": self.membership.pk, "is_project_manager": "on"},
                ).status_code,
                302,
            )
        entry = ProjectInternalAccess.objects.get(
            project=self.project, membership=self.membership
        )
        self.assertTrue(entry.can_manage and entry.can_invite_clients)
        self.assertTrue(access.can_manage_project(self.pm, self.project))
        self.assertTrue(access.can_invite_clients(self.pm, self.project))
        self.client.force_login(self.pm)
        for name in (
            "people",
            "edit",
            "invite_client",
            "selection_create",
            "document_create",
            "daily_log_create",
            "job_site_manage",
        ):
            self.assertEqual(
                self.client.get(
                    reverse("projects:" + name, args=[self.project.pk])
                ).status_code,
                200,
                name,
            )

    def test_model_save_and_partial_updates_preserve_pm_guarantee(self):
        entry = self.assign()
        self.assertTrue(entry.can_manage and entry.can_invite_clients)
        entry.can_manage = entry.can_invite_clients = False
        entry.save(update_fields=["can_manage", "can_invite_clients"])
        entry.refresh_from_db()
        self.assertTrue(entry.can_manage and entry.can_invite_clients)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProjectInternalAccess.objects.filter(pk=entry.pk).update(can_manage=False)

    def test_pm_creator_is_assigned_without_admin_intervention(self):
        self.client.force_login(self.pm)
        response = self.client.post(
            reverse("projects:create"),
            {
                "organization": self.org.pk,
                "name": "PM-created job",
                "status": "planning",
            },
        )
        self.assertEqual(response.status_code, 302)
        job = Project.objects.get(name="PM-created job")
        entry = job.internal_access.get(membership=self.membership)
        self.assertTrue(
            entry.is_project_manager and entry.can_manage and entry.can_invite_clients
        )
        self.assertEqual(entry.assigned_by, self.pm)

    def test_pm_copy_creator_is_assigned_and_admin_creator_is_not_automatically_pm(
        self,
    ):
        self.assign()
        with transaction.atomic():
            target = copy_setup(
                self.project, self.pm, {"name": "PM copy", "selections": []}
            )
        self.assertTrue(
            target.internal_access.get(membership=self.membership).is_project_manager
        )
        self.client.force_login(self.admin)
        self.client.post(
            reverse("projects:create"),
            {"organization": self.org.pk, "name": "Admin job", "status": "planning"},
        )
        entry = Project.objects.get(name="Admin job").internal_access.get(
            membership__user=self.admin
        )
        self.assertFalse(entry.is_project_manager)

    def test_existing_manager_can_assign_themselves_pm(self):
        ProjectInternalAccess.objects.create(
            project=self.project, membership=self.membership, can_manage=True
        )
        self.client.force_login(self.pm)
        with patch("projects.views.send_project_assignment_notification"):
            response = self.client.post(
                self.url, {"membership": self.membership.pk, "is_project_manager": "on"}
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            self.project.internal_access.get(
                membership=self.membership
            ).is_project_manager
        )

    def test_all_internal_roles_assigned_pm_can_handle_project_workflows(self):
        self.assign()
        for role in OrganizationMembership.INTERNAL_ROLES:
            self.membership.role = role
            self.membership.save()
            self.assertTrue(can_copy_project(self.pm, self.project))
            for check in (
                access.can_manage_project,
                access.can_invite_clients,
                access.can_use_project_messaging,
                access.can_use_selections,
                access.can_manage_schedule,
                access.can_manage_daily_logs,
            ):
                self.assertTrue(check(self.pm, self.project), (role, check.__name__))

    def test_pm_assignment_does_not_grant_company_admin_or_quickbooks(self):
        self.assign()
        self.assertFalse(access.can_manage_organization(self.pm, self.org))
        self.assertFalse(access.accounting_organizations_for_user(self.pm).exists())
        self.assertFalse(self.membership.can_manage_desktop)
        self.membership.project_budget_view = False
        self.membership.save()
        self.assertFalse(access.can_view_project_budget(self.pm, self.project))

    def test_unassigned_pm_cannot_claim_someone_elses_project(self):
        self.client.force_login(self.pm)
        response = self.client.post(
            self.url, {"membership": self.membership.pk, "is_project_manager": "on"}
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            self.project.internal_access.filter(membership=self.membership).exists()
        )
        self.assign()
        self.assertFalse(access.can_manage_project(self.pm, self.foreign))

    def test_revoked_and_inactive_memberships_remain_blocked(self):
        entry = self.assign()
        entry.is_active = False
        entry.save()
        self.assertFalse(access.can_manage_project(self.pm, self.project))
        self.assertFalse(access.can_invite_clients(self.pm, self.project))
        entry.is_active = True
        entry.save()
        self.membership.is_active = False
        self.membership.save()
        self.assertFalse(access.can_manage_project(self.pm, self.project))

    def test_non_pm_permissions_remain_independent_and_preview_read_only(self):
        form = ProjectInternalAccessForm(
            {"membership": self.membership.pk, "can_invite_clients": "on"},
            project=self.project,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.cleaned_data["can_manage"])
        self.assertTrue(form.cleaned_data["can_invite_clients"])
        self.assign()
        self.client.force_login(self.pm)
        session = self.client.session
        session[SESSION_KEY] = self.project.pk
        session.save()
        self.assertEqual(
            self.client.post(
                self.url, {"membership": self.membership.pk, "is_project_manager": "on"}
            ).status_code,
            403,
        )
