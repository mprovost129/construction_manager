from django.contrib.auth import get_user_model
from django.test import TestCase
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
