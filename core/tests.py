from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


class BaseTemplatePartialTests(TestCase):
    def test_anonymous_page_renders_shared_partials(self):
        response = self.client.get(reverse('core:home'))

        self.assertContains(response, 'aria-label="Primary navigation"')
        self.assertContains(response, 'bootstrap@5.3.3')
        self.assertContains(response, 'static/css/main.css')
        self.assertContains(response, 'static/js/main.js')
        self.assertContains(response, 'Log in')

    def test_authenticated_header_renders_from_partial(self):
        user = get_user_model().objects.create_user(
            email='member@example.com',
            password='password',
        )
        self.client.force_login(user)
        response = self.client.get(reverse('core:home'))

        self.assertContains(response, 'member@example.com')
        self.assertContains(response, 'action="/accounts/logout/"')


@override_settings(
    LEGAL_BUSINESS_NAME='Example Construction LLC',
    LEGAL_CONTACT_EMAIL='privacy@example.com',
    LEGAL_BUSINESS_ADDRESS='123 Main Street, Albany, NY 12207',
    LEGAL_GOVERNING_LAW='State of New York, United States',
    LEGAL_EFFECTIVE_DATE='July 31, 2026',
)
class PublicLegalPageTests(TestCase):
    def test_eula_is_public_and_contains_required_app_terms(self):
        response = self.client.get(reverse('core:eula'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'End-User License Agreement')
        self.assertContains(response, 'Example Construction LLC')
        self.assertContains(response, 'QuickBooks Online')
        self.assertContains(response, 'privacy@example.com')
        self.assertContains(response, 'State of New York, United States')

    def test_privacy_policy_is_public_and_covers_qbo_retention_and_deletion(self):
        response = self.client.get(reverse('core:privacy'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Privacy Policy')
        self.assertContains(response, 'OAuth')
        self.assertContains(response, 'Disconnecting QuickBooks')
        self.assertContains(response, 'Retention and deletion')
        self.assertContains(response, 'privacy@example.com')

    def test_public_footer_links_to_both_legal_pages(self):
        response = self.client.get(reverse('core:home'))

        self.assertContains(response, reverse('core:eula'))
        self.assertContains(response, reverse('core:privacy'))
