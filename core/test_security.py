from datetime import timedelta
from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from core.models import TurnstileRedemption
from core.security import verify_form_token
from projects.models import Organization, OrganizationMembership, Project


@override_settings(
    TURNSTILE_FORMS_ENABLED=True,
    TURNSTILE_SITE_KEY='test-site-key',
    TURNSTILE_SECRET_KEY='test-secret-key',
    ALLOWED_HOSTS=['testserver'],
)
class FormSecurityTests(TestCase):
    def request(self, token='test-token'):
        return RequestFactory().post('/accounts/login/', {'cf-turnstile-response': token})

    def valid_response(self, **changes):
        data = {'success': True, 'hostname': 'testserver', 'action': 'heritage_form'}
        data.update(changes)
        return Mock(ok=True, json=Mock(return_value=data))

    @patch('core.security.requests.post')
    def test_rejects_missing_oversized_or_failed_verification(self, post):
        for token in ('', 'x' * 2049):
            self.assertFalse(verify_form_token(self.request(token)))
        post.assert_not_called()
        for response in (
            self.valid_response(success=False), self.valid_response(success='true'),
            self.valid_response(hostname='unrelated.example'),
            self.valid_response(action='other_action'),
            Mock(ok=False), Mock(ok=True, json=Mock(return_value=[])),
            Mock(ok=True, json=Mock(side_effect=ValueError)),
        ):
            with self.subTest(response=response):
                post.return_value = response
                self.assertFalse(verify_form_token(self.request()))
        post.side_effect = requests.Timeout
        self.assertFalse(verify_form_token(self.request()))
        self.assertEqual(TurnstileRedemption.objects.count(), 0)

    @patch('core.security.requests.post')
    def test_valid_token_is_single_use_even_if_cloudflare_accepts_replay(self, post):
        post.return_value = self.valid_response()
        self.assertTrue(verify_form_token(self.request()))
        self.assertFalse(verify_form_token(self.request()))
        record = TurnstileRedemption.objects.get()
        self.assertNotEqual(record.digest, 'test-token')
        self.assertEqual(len(record.digest), 64)
        self.assertNotIn('remoteip', post.call_args.kwargs['data'])
        TurnstileRedemption.objects.create(digest='old', expires_at=timezone.now() - timedelta(days=1))
        self.assertTrue(verify_form_token(self.request('another-token')))
        self.assertFalse(TurnstileRedemption.objects.filter(digest='old').exists())

    @patch('core.security.requests.post')
    def test_database_failure_does_not_allow_submission(self, post):
        post.return_value = self.valid_response()
        with patch('core.security.TurnstileRedemption.objects.create', side_effect=DatabaseError):
            self.assertFalse(verify_form_token(self.request()))

    @override_settings(TURNSTILE_SECRET_KEY='')
    @patch('core.security.requests.post')
    def test_missing_configuration_fails_closed(self, post):
        self.assertFalse(verify_form_token(self.request()))
        post.assert_not_called()

    @patch('core.security.requests.post')
    def test_login_and_password_reset_block_before_authentication_or_email(self, post):
        user = get_user_model().objects.create_user('security@example.com', 'test-password')
        response = self.client.post(reverse('login'), {
            'username': user.email, 'password': 'test-password',
        })
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('_auth_user_id', self.client.session)
        response = self.client.post(reverse('password_reset'), {'email': user.email})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(mail.outbox), 0)
        post.assert_not_called()
        post.return_value = self.valid_response()
        response = self.client.post(reverse('login'), {
            'username': user.email, 'password': 'test-password',
            'cf-turnstile-response': 'login-token',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session['_auth_user_id'], str(user.pk))

    @patch('core.security.requests.post')
    def test_protected_mutations_include_admin_and_authenticated_forms(self, post):
        for name in ('login', 'admin:login', 'password_reset', 'logout'):
            with self.subTest(name=name):
                self.assertEqual(self.client.post(reverse(name), {}).status_code, 403)
        company = Organization.objects.create(name='Security test', slug='security-test')
        project = Project.objects.create(organization=company, name='Original name')
        admin = get_user_model().objects.create_user('admin-security@example.com', 'test-password')
        OrganizationMembership.objects.create(organization=company, user=admin, role='admin')
        self.client.force_login(admin)
        response = self.client.post(reverse('projects:edit', args=[project.pk]), {'name': 'Changed'})
        self.assertEqual(response.status_code, 403)
        project.refresh_from_db()
        self.assertEqual(project.name, 'Original name')
        # JSON and AJAX headers must not turn a browser endpoint into an exemption.
        response = self.client.post(reverse('projects:edit', args=[project.pk]),
                                    data='{}', content_type='application/json',
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 403)
        post.assert_not_called()

    @patch('core.security.requests.post')
    def test_machine_protocols_remain_disabled_without_browser_exception_bypass(self, post):
        for name in ('integrations:desktop_connector', 'subscriptions:stripe_webhook'):
            url = reverse(name)
            self.assertEqual(resolve(url).view_name, name)
            response = self.client.post(url, data='not authenticated', content_type='text/xml')
            self.assertEqual(response.status_code, 404)
        post.assert_not_called()

    def test_login_admin_and_reset_pages_load_form_protection_and_no_store(self):
        for name in ('login', 'admin:login', 'password_reset'):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'form-security.js')
                self.assertContains(response, 'challenges.cloudflare.com/turnstile/v0/api.js')
                self.assertNotContains(response, 'test-secret-key')
                self.assertIn('no-store', response['Cache-Control'])
                self.assertIn('private', response['Cache-Control'])
                self.assertEqual(response['Referrer-Policy'], 'same-origin')

    @patch('core.security.requests.post')
    def test_multipart_upload_requires_verification_and_preserves_file(self, post):
        company = Organization.objects.create(name='Upload test', slug='upload-test')
        project = Project.objects.create(organization=company, name='Upload project')
        admin = get_user_model().objects.create_user('upload-security@example.com', 'test-password')
        OrganizationMembership.objects.create(organization=company, user=admin, role='admin')
        self.client.force_login(admin)
        url = reverse('projects:document_create', args=[project.pk])
        with self.settings(PRIVATE_STORAGE_BACKEND='r2', STORAGES={**settings.STORAGES, 'private_documents': {
            'BACKEND': 'django.core.files.storage.InMemoryStorage',
        }}):
            payload = {'title': 'Private test upload', 'category': 'general',
                       'file': SimpleUploadedFile('test.txt', b'Synthetic file')}
            self.assertEqual(self.client.post(url, payload).status_code, 403)
            self.assertEqual(project.documents.count(), 0)
            post.return_value = self.valid_response()
            payload['cf-turnstile-response'] = 'multipart-test-token'
            payload['file'] = SimpleUploadedFile('test.txt', b'Synthetic file')
            response = self.client.post(url, payload)
            self.assertEqual(response.status_code, 302)
            document = project.documents.get()
            self.assertFalse(document.client_visible)
            self.assertEqual(document.versions.get().file.read(), b'Synthetic file')
