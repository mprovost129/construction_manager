from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from integrations.models import QuickBooksConnection, QuickBooksDesktopOnboarding
from projects.models import Organization, OrganizationMembership


@override_settings(QUICKBOOKS_INTEGRATION_ENABLED=False)
class DesktopOnboardingTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Heritage', slug='heritage')
        self.other = Organization.objects.create(name='Other', slug='other')
        self.admin = get_user_model().objects.create_user('admin@example.com')
        self.customer = get_user_model().objects.create_user('customer@example.com')
        OrganizationMembership.objects.create(organization=self.org, user=self.admin, role='admin')
        OrganizationMembership.objects.create(organization=self.org, user=self.customer, role='client')
        self.url = reverse('integrations:quickbooks_setup')
        self.client.force_login(self.admin)

    def test_admin_can_save_partial_details_without_connecting(self):
        self.assertContains(self.client.get(self.url), 'Not connected')
        self.assertFalse(QuickBooksDesktopOnboarding.objects.exists())
        response = self.client.post(self.url, {'organization': 'heritage', 'hosting': 'office'})
        self.assertEqual(response.status_code, 302)
        saved = QuickBooksDesktopOnboarding.objects.get()
        self.assertEqual(saved.organization, self.org)
        self.assertEqual(saved.updated_by, self.admin)
        self.assertEqual(saved.hosting, 'office')
        self.assertIn('QuickBooks edition and year', saved.missing_details)
        self.assertFalse(QuickBooksConnection.objects.exists())
        self.assertContains(self.client.get(self.url), 'Setup details saved.')

    def test_clients_cannot_read_or_edit_setup(self):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {'organization': 'heritage'}).status_code, 403)
        self.assertNotContains(self.client.get('/'), 'QuickBooks setup')
        self.assertFalse(QuickBooksDesktopOnboarding.objects.exists())

    def test_company_admin_cannot_access_another_company(self):
        self.assertEqual(self.client.get(self.url + '?organization=other').status_code, 404)
        self.assertEqual(self.client.post(self.url, {'organization': 'other'}).status_code, 404)
        self.assertFalse(QuickBooksDesktopOnboarding.objects.exists())

    def test_invalid_file_count_does_not_save(self):
        response = self.client.post(self.url, {'organization': 'heritage', 'company_file_count': '0'})
        self.assertContains(response, 'Enter at least one company file')
        self.assertFalse(QuickBooksDesktopOnboarding.objects.exists())

    def test_online_authorization_is_unavailable_for_desktop_deployment(self):
        with patch('integrations.views.QuickBooksOAuthClient') as oauth:
            response = self.client.post(reverse('integrations:quickbooks_authorize'), {'organization': 'heritage'})
        self.assertEqual(response.status_code, 404)
        oauth.assert_not_called()
        self.assertRedirects(self.client.get(reverse('integrations:quickbooks_connect')), self.url)

    def test_complete_intake_still_requires_connection_testing(self):
        data = {'organization': 'heritage', 'edition_year': 'Enterprise 2024',
                'hosting': 'hosted', 'workstation': 'Example host', 'company_file_count': '1',
                'setup_contact': 'accountant@example.com', 'customer_structure': 'Customer/jobs',
                'item_tax_notes': 'Review service items and tax', 'numbering_credits': 'Accountant review'}
        self.client.post(self.url, data)
        self.assertEqual(QuickBooksDesktopOnboarding.objects.get().missing_details, [])
        response = self.client.get(self.url)
        self.assertContains(response, 'Not connected')
        self.assertContains(response, 'Connection development and accounting validation are still required.')
