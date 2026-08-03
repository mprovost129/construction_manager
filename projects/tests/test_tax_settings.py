from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from projects.models import Organization, OrganizationMembership


class CompanyTaxSettingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(
            name='Example Builders', slug='example-builders-tax'
        )
        cls.admin_user = get_user_model().objects.create_user(
            'admin-tax@example.com', 'password'
        )
        cls.staff_user = get_user_model().objects.create_user(
            'staff-tax@example.com', 'password'
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

    def test_default_tax_rate_starts_at_zero(self):
        self.assertEqual(self.organization.default_tax_rate, Decimal('0'))

    def test_admin_can_update_default_tax_rate(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse('projects:company_tax_settings', args=(self.organization.slug,)),
            {'default_tax_rate': '7.250'},
        )
        self.assertEqual(response.status_code, 302)
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.default_tax_rate, Decimal('7.250'))

    def test_staff_cannot_update_tax_settings(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(
            reverse('projects:company_tax_settings', args=(self.organization.slug,))
        )
        self.assertEqual(response.status_code, 403)

    def test_rate_over_100_rejected(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse('projects:company_tax_settings', args=(self.organization.slug,)),
            {'default_tax_rate': '150.000'},
        )
        self.assertEqual(response.status_code, 200)
        self.organization.refresh_from_db()
        self.assertEqual(self.organization.default_tax_rate, Decimal('0'))
