from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from projects.models import CostCode, Organization, OrganizationMembership


class CostCodeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(
            name='Example Builders', slug='example-builders-cc'
        )
        cls.other_organization = Organization.objects.create(
            name='Other Builders', slug='other-builders-cc'
        )
        cls.admin_user = get_user_model().objects.create_user(
            'admin-cc@example.com', 'password'
        )
        cls.staff_user = get_user_model().objects.create_user(
            'staff-cc@example.com', 'password'
        )
        OrganizationMembership.objects.create(
            organization=cls.organization,
            user=cls.admin_user,
            role=OrganizationMembership.Role.ADMIN,
        )
        OrganizationMembership.objects.create(
            organization=cls.organization,
            user=cls.staff_user,
            role=OrganizationMembership.Role.STAFF,
        )

    def test_admin_creates_cost_code(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse('projects:cost_code_create', args=(self.organization.slug,)),
            {'code': '02-200', 'name': 'Excavation', 'description': '', 'is_active': 'on'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            CostCode.objects.filter(organization=self.organization, code='02-200').exists()
        )

    def test_staff_cannot_create_cost_code(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(
            reverse('projects:cost_code_create', args=(self.organization.slug,))
        )
        self.assertEqual(response.status_code, 403)

    def test_duplicate_code_rejected(self):
        CostCode.objects.create(
            organization=self.organization, code='02-200', name='Excavation'
        )
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse('projects:cost_code_create', args=(self.organization.slug,)),
            {'code': '02-200', 'name': 'Duplicate', 'description': '', 'is_active': 'on'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already has a cost code')

    def test_edit_can_deactivate_cost_code(self):
        cost_code = CostCode.objects.create(
            organization=self.organization, code='02-200', name='Excavation'
        )
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse(
                'projects:cost_code_edit',
                args=(self.organization.slug, cost_code.pk),
            ),
            {'code': '02-200', 'name': 'Excavation', 'description': '', 'is_active': ''},
        )
        self.assertEqual(response.status_code, 302)
        cost_code.refresh_from_db()
        self.assertFalse(cost_code.is_active)

    def test_admin_cannot_manage_another_companys_cost_codes(self):
        cost_code = CostCode.objects.create(
            organization=self.other_organization, code='03-300', name='Framing'
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse(
                'projects:cost_code_edit',
                args=(self.other_organization.slug, cost_code.pk),
            )
        )
        self.assertEqual(response.status_code, 404)
