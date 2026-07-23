from django.contrib.auth import get_user_model
from django.test import TestCase

from projects.access import projects_for_user
from projects.models import (
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
)


class ProjectAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(
            name='Example Builders',
            slug='example-builders',
        )
        cls.other_organization = Organization.objects.create(
            name='Other Builders',
            slug='other-builders',
        )
        cls.project = Project.objects.create(
            organization=cls.organization,
            name='Oak Street',
            status=Project.Status.ACTIVE,
        )
        cls.second_project = Project.objects.create(
            organization=cls.organization,
            name='Pine Street',
        )
        cls.other_project = Project.objects.create(
            organization=cls.other_organization,
            name='Cedar Street',
        )
        cls.staff = get_user_model().objects.create_user(
            'STAFF@EXAMPLE.COM',
            'password',
        )
        cls.client_user = get_user_model().objects.create_user(
            'client@example.com',
            'password',
        )
        OrganizationMembership.objects.create(
            organization=cls.organization,
            user=cls.staff,
            role=OrganizationMembership.Role.STAFF,
        )
        OrganizationMembership.objects.create(
            organization=cls.organization,
            user=cls.client_user,
            role=OrganizationMembership.Role.CLIENT,
        )
        ProjectMembership.objects.create(
            project=cls.project,
            user=cls.client_user,
            role=OrganizationMembership.Role.CLIENT,
        )

    def test_user_manager_normalizes_email_for_login(self):
        self.assertEqual(self.staff.email, 'staff@example.com')
        self.assertEqual(get_user_model().USERNAME_FIELD, 'email')

    def test_internal_staff_can_access_all_projects_for_their_company(self):
        projects = projects_for_user(self.staff)
        self.assertQuerySetEqual(
            projects.order_by('name'),
            [self.project, self.second_project],
        )

    def test_client_can_only_access_assigned_project(self):
        self.assertQuerySetEqual(projects_for_user(self.client_user), [self.project])

    def test_staff_project_financial_access_excludes_company_financials(self):
        membership = self.staff.organization_memberships.get()
        self.assertTrue(membership.can_view_project_financials)
        self.assertFalse(membership.can_view_company_financials)

    def test_client_cannot_open_unassigned_project(self):
        self.client.force_login(self.client_user)
        response = self.client.get(f'/projects/{self.second_project.pk}/')
        self.assertEqual(response.status_code, 404)

    def test_project_page_hides_project_financial_module_from_client(self):
        self.client.force_login(self.client_user)
        response = self.client.get(f'/projects/{self.project.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Project financials')

    def test_project_page_shows_project_financial_module_to_staff(self):
        self.client.force_login(self.staff)
        response = self.client.get(f'/projects/{self.project.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Project financials')
