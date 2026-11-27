from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from projects.access import projects_for_user
from projects.models import Organization, OrganizationMembership, Project
from subscriptions.checks import stripe_subscription_configuration_check
from subscriptions.services import (
    StripeSubscriptionUnavailable,
    validate_stripe_configuration,
)


@override_settings(SUBSCRIPTION_BILLING_ENABLED=False, STRIPE_ENFORCE_SUBSCRIPTIONS=True,
                   STRIPE_SUBSCRIPTIONS_CONFIGURED=True)
class DedicatedDeploymentTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('admin@example.com', 'password')
        self.org = Organization.objects.create(name='Heritage', slug='heritage')
        OrganizationMembership.objects.create(organization=self.org, user=self.admin, role='admin')
        self.project = Project.objects.create(organization=self.org, name='Test home')
        self.client.force_login(self.admin)

    def test_no_billing_ui_or_direct_endpoint_even_for_admin(self):
        response = self.client.get(reverse('projects:company_team', args=['heritage']))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Subscription')
        self.assertContains(response, 'QuickBooks setup')
        for name, method in [('detail', 'get'), ('checkout_create', 'post'), ('portal_create', 'post')]:
            self.assertEqual(getattr(self.client, method)(reverse(f'subscriptions:{name}', args=['heritage'])).status_code, 404)
        with patch('subscriptions.views.process_stripe_webhook') as process:
            response = self.client.post(reverse('subscriptions:stripe_webhook'), '{}', content_type='application/json')
        self.assertEqual(response.status_code, 404)
        process.assert_not_called()
        response = self.client.get('/admin/')
        self.assertNotContains(response, 'Stripe')
        self.assertNotContains(response, 'Subscription')

    def test_access_never_depends_on_billing(self):
        from subscriptions.entitlements import (
            entitled_organization_ids,
            organization_has_access,
        )
        self.assertIsNone(entitled_organization_ids())
        self.assertTrue(organization_has_access(self.org))
        self.assertIn(self.project, projects_for_user(self.admin))
        self.assertEqual(stripe_subscription_configuration_check(None), [])
        with self.assertRaises(StripeSubscriptionUnavailable):
            validate_stripe_configuration()

    def test_help_and_terms_have_no_subscription_copy(self):
        for url in ['/help/', '/legal/eula/']:
            response = self.client.get(url)
            for text in ['Stripe', 'SaaS', 'subscription']:
                self.assertNotContains(response, text)
