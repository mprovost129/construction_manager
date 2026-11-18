import base64
import time
from datetime import timedelta
from io import StringIO

from cryptography.fernet import InvalidToken
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from projects.models import Organization, OrganizationMembership
from users.mfa import issue_recovery_codes
from users.models import AuthenticatorDevice, MFAAuditEvent


@override_settings(MFA_REQUIRED_FOR_ADMINS=True)
class MFATests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('admin@example.com', 'Example-password-123!', is_staff=True)
        self.client.force_login(self.user)
        self.setup_url = reverse('users:mfa_setup')
        self.verify_url = reverse('users:mfa_verify')

    def device(self, confirmed=True):
        return AuthenticatorDevice.objects.create(account=self.user, confirmed=confirmed, name='Test')

    def code(self, device):
        return f'{totp(device.bin_key):06d}'

    def start(self):
        response = self.client.post(self.setup_url, {'step': 'start', 'password': 'Example-password-123!'})
        self.assertEqual(response.status_code, 200)
        return AuthenticatorDevice.objects.get(account=self.user), response

    def confirm(self):
        device, _ = self.start()
        response = self.client.post(self.setup_url, {'step': 'confirm', 'code': self.code(device)})
        return device, response

    def test_admin_routes_and_all_mutations_require_enrollment(self):
        for path in ['/', '/companies/', '/admin/', '/operations/', '/projects/1/', '/projects/1/documents/1/versions/1/download/', '/accounts/login/']:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith(self.setup_url))
        for method in ['post', 'put', 'patch', 'delete']:
            with self.subTest(method=method):
                response = getattr(self.client, method)('/projects/1/', data='{}', content_type='application/json')
                self.assertEqual(response.status_code, 403)
                self.assertContains(response, 'This submission was not saved', status_code=403)

    def test_company_admin_requires_mfa_without_django_staff(self):
        self.user.is_staff = False
        self.user.save()
        organization = Organization.objects.create(name='Test', slug='test')
        membership = OrganizationMembership.objects.create(organization=organization, user=self.user, role='admin')
        self.assertEqual(self.client.get('/').status_code, 302)
        membership.is_active = False
        membership.save()
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_clients_unenrolled_and_anonymous_are_not_forced_to_enroll(self):
        self.user.is_staff = False
        self.user.save()
        self.assertEqual(self.client.get('/').status_code, 200)
        self.client.logout()
        response = self.client.get(self.setup_url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/accounts/login/'))
        self.assertEqual(self.client.get('/health/').status_code, 200)

    def test_enrolled_clients_require_mfa_even_when_admin_policy_disabled(self):
        self.user.is_staff = False
        self.user.save()
        self.device()
        with override_settings(MFA_REQUIRED_FOR_ADMINS=False):
            self.assertTrue(self.client.get('/').url.startswith(self.verify_url))

    def test_setup_get_has_no_seed_or_database_side_effect(self):
        response = self.client.get(self.setup_url)
        self.assertContains(response, 'Current password')
        self.assertNotContains(response, 'data:image/png')
        self.assertFalse(AuthenticatorDevice.objects.exists())
        self.assertNotContains(response, 'Notifications')
        self.assertIn('no-store', response['Cache-Control'])

    def test_wrong_password_cannot_enroll(self):
        response = self.client.post(self.setup_url, {'step': 'start', 'password': 'Wrong-password'})
        self.assertContains(response, 'could not verify your password')
        self.assertFalse(AuthenticatorDevice.objects.exists())

    def test_setup_seed_encrypted_and_not_exposed_in_another_session(self):
        device, response = self.start()
        secret = device.bin_key
        self.assertContains(response, 'data:image/png;base64,')
        self.assertContains(response, base64.b32encode(secret).decode())
        self.assertEqual(TOTPDevice.objects.get(pk=device.pk).key, '')
        self.assertNotIn(secret.hex(), device.encrypted_key)
        self.assertFalse(device.confirmed)
        other = Client()
        other.force_login(self.user)
        self.assertNotContains(other.get(self.setup_url), base64.b32encode(secret).decode())
        response = other.post(self.setup_url, {'step': 'confirm', 'code': self.code(device)})
        self.assertContains(response, 'Setup expired')
        device.refresh_from_db()
        self.assertFalse(device.confirmed)

    def test_expired_setup_cannot_be_confirmed(self):
        device, _ = self.start()
        device.setup_started_at = timezone.now() - timedelta(minutes=11)
        device.save()
        self.assertContains(self.client.post(self.setup_url, {'step': 'confirm', 'code': self.code(device)}), 'Setup expired')
        self.assertNotContains(self.client.get(self.setup_url), 'data:image/png')

    def test_enrollment_verifies_rotates_session_and_issues_hashed_codes(self):
        old_session = self.client.session.session_key
        device, response = self.confirm()
        device.refresh_from_db()
        self.assertTrue(device.confirmed)
        self.assertNotEqual(old_session, self.client.session.session_key)
        self.assertEqual(len(response.context['codes']), 10)
        self.assertContains(response, 'Download codes (.txt)')
        self.assertContains(response, 'Print codes')
        self.assertContains(response, 'data-recovery-account')
        self.assertIn('no-store', response['Cache-Control'])
        for recovery_code in response.context['codes']:
            self.assertContains(response, f'<code>{recovery_code}</code>', count=1)
        self.assertEqual(device.recovery_codes.count(), 10)
        self.assertTrue(all(len(x) == 64 for x in device.recovery_codes.values_list('digest', flat=True)))
        self.assertTrue(MFAAuditEvent.objects.filter(event='enrolled').exists())
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertNotContains(self.client.get(reverse('users:security')), response.context['codes'][0])
        self.assertNotContains(self.client.get(reverse('users:mfa_recovery_codes')), 'data-recovery-download')
        self.assertNotContains(self.client.get(self.setup_url, follow=True), 'data:image/png')

    def test_login_success_still_requires_mfa_and_password_reset_does_not_disable_it(self):
        self.device()
        self.client.logout()
        self.assertEqual(self.client.post('/accounts/login/', {'username': self.user.email, 'password': 'Example-password-123!'}).status_code, 302)
        self.assertTrue(self.client.get('/').url.startswith(self.verify_url))
        self.user.set_password('New-valid-password!')
        self.user.save()
        self.client.force_login(self.user)
        self.assertTrue(self.client.get('/').url.startswith(self.verify_url))

    def test_totp_replay_rejected_in_second_session(self):
        device = self.device()
        code = self.code(device)
        self.assertEqual(self.client.post(self.verify_url, {'code': code}).status_code, 302)
        other = Client()
        other.force_login(self.user)
        self.assertContains(other.post(self.verify_url, {'code': code}), 'could not be verified')
        self.assertTrue(other.get('/').url.startswith(self.verify_url))

    def test_failures_throttle_totp_and_recovery_together(self):
        device = self.device()
        codes = issue_recovery_codes(device)
        self.assertContains(self.client.post(self.verify_url, {'code': 'invalid'}), 'could not be verified')
        self.assertContains(self.client.post(self.verify_url, {'code': codes[0], 'use_recovery': 'on'}), 'could not be verified')
        self.assertEqual(device.recovery_codes.count(), 10)
        device.refresh_from_db()
        self.assertEqual(device.throttling_failure_count, 1)

    def test_recovery_codes_are_one_use_and_not_a_replacement_for_password(self):
        device = self.device()
        codes = issue_recovery_codes(device)
        anonymous = Client()
        self.assertEqual(anonymous.post(self.verify_url, {'code': codes[0], 'use_recovery': 'on'}).status_code, 302)
        self.assertEqual(device.recovery_codes.count(), 10)
        self.assertEqual(self.client.post(self.verify_url, {'code': codes[0], 'use_recovery': 'on'}).status_code, 302)
        self.assertEqual(device.recovery_codes.count(), 9)
        self.client.logout()
        self.client.force_login(self.user)
        self.assertContains(self.client.post(self.verify_url, {'code': codes[0], 'use_recovery': 'on'}), 'could not be verified')

    def test_expired_verification_and_removed_device_invalidate_session(self):
        device = self.device()
        self.client.post(self.verify_url, {'code': self.code(device)})
        session = self.client.session
        session['mfa_at'] = time.time() - 9 * 3600
        session.save()
        self.assertTrue(self.client.get('/').url.startswith(self.verify_url))
        device.delete()
        self.assertTrue(self.client.get('/').url.startswith(self.setup_url))

    def test_user_or_device_id_mismatch_cannot_skip_verification(self):
        device = self.device()
        self.client.post(self.verify_url, {'code': self.code(device)})
        for field in ['mfa_device', 'mfa_user']:
            with self.subTest(field=field):
                session = self.client.session
                prior = session[field]
                session[field] = 999999
                session.save()
                self.assertTrue(self.client.get('/').url.startswith(self.verify_url))
                session[field] = prior
                session.save()

    def test_foreign_device_fields_are_ignored(self):
        device = self.device()
        stranger = get_user_model().objects.create_user('stranger@example.com', 'Password!')
        other = AuthenticatorDevice.objects.create(account=stranger, confirmed=True)
        self.client.post(self.verify_url, {'code': self.code(other), 'device': other.pk})
        self.assertNotIn('mfa_device', self.client.session)
        device.refresh_from_db()
        self.assertEqual(device.throttling_failure_count, 1)

    def test_recovery_regeneration_requires_password_and_new_code(self):
        device, response = self.confirm()
        codes = response.context['codes']
        url = reverse('users:mfa_recovery_codes')
        self.assertContains(self.client.post(url, {'password': 'Wrong', 'code': codes[0], 'use_recovery': 'on'}), 'could not verify your password')
        old_digests = set(device.recovery_codes.values_list('digest', flat=True))
        response = self.client.post(url, {'password': 'Example-password-123!', 'code': codes[0], 'use_recovery': 'on'})
        self.assertContains(response, 'Save your recovery codes')
        self.assertFalse(old_digests & set(device.recovery_codes.values_list('digest', flat=True)))

    def test_external_next_url_rejected(self):
        device = self.device()
        response = self.client.post(self.verify_url, {'code': self.code(device), 'next': 'https://evil.example/'})
        self.assertEqual(response.url, '/')

    def test_csrf_and_turnstile_protect_mfa_submissions(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(self.setup_url, {'step': 'start'}).status_code, 403)
        with override_settings(TURNSTILE_FORMS_ENABLED=True):
            self.assertEqual(self.client.post(self.setup_url, {'step': 'start', 'password': 'Example-password-123!'}).status_code, 403)
        self.assertFalse(AuthenticatorDevice.objects.exists())

    def test_encryption_rotation_requires_old_key_and_works_with_fallback(self):
        device = self.device()
        seed = device.bin_key
        from django.conf import settings
        old_key = settings.SECRET_KEY
        with override_settings(SECRET_KEY='replacement-app-key', SECRET_KEY_FALLBACKS=[]):
            with self.assertRaises(InvalidToken):
                _ = device.bin_key
        with override_settings(SECRET_KEY='replacement-app-key', SECRET_KEY_FALLBACKS=[old_key]):
            self.assertEqual(device.bin_key, seed)

    def test_admin_cannot_edit_or_read_raw_authenticator_models(self):
        self.assertFalse(admin.site.is_registered(TOTPDevice))
        self.assertFalse(admin.site.is_registered(AuthenticatorDevice))

    def test_machine_protocols_retain_their_own_disabled_gates(self):
        response = self.client.post(reverse('integrations:desktop_connector'), data='<xml/>', content_type='text/xml')
        self.assertEqual(response.status_code, 404)

    def test_logout_works_before_mfa_enrollment(self):
        self.assertEqual(self.client.post('/accounts/logout/').status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_existing_confirmed_device_cannot_be_replaced_through_setup(self):
        device = self.device()
        response = self.client.post(self.setup_url, {'step': 'start', 'password': 'Example-password-123!'})
        self.assertEqual(response.url, self.verify_url)
        self.assertEqual(AuthenticatorDevice.objects.get(account=self.user).pk, device.pk)

    def test_valid_internal_next_is_preserved(self):
        device = self.device()
        response = self.client.post(self.verify_url, {'code': self.code(device), 'next': '/companies/'})
        self.assertEqual(response.url, '/companies/')

    def test_operations_permission_requires_mfa_even_without_staff_flag(self):
        self.user.is_staff = False
        self.user.save()
        self.user.user_permissions.add(Permission.objects.get(codename='view_operations'))
        self.assertTrue(self.client.get('/operations/').url.startswith(self.setup_url))

    def test_key_rewrap_is_dry_run_by_default_and_supports_removing_fallback(self):
        from django.conf import settings
        old_key = settings.SECRET_KEY
        device = self.device()
        seed, encrypted = device.bin_key, device.encrypted_key
        with override_settings(SECRET_KEY='new-app-key', SECRET_KEY_FALLBACKS=[old_key]):
            output = StringIO()
            call_command('rewrap_mfa_keys', stdout=output)
            device.refresh_from_db()
            self.assertEqual(device.encrypted_key, encrypted)
            self.assertNotIn(base64.b32encode(seed).decode(), output.getvalue())
            call_command('rewrap_mfa_keys', apply=True, stdout=output)
            device.refresh_from_db()
            self.assertNotEqual(device.encrypted_key, encrypted)
        with override_settings(SECRET_KEY='new-app-key', SECRET_KEY_FALLBACKS=[]):
            self.assertEqual(device.bin_key, seed)

    def test_captcha_failure_does_not_expose_workspace_navigation_or_messages(self):
        with override_settings(TURNSTILE_FORMS_ENABLED=True):
            response = self.client.post(self.setup_url, {'step': 'start'})
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, 'Notifications', status_code=403)
        self.assertNotContains(response, '/companies/', status_code=403)

    def test_successful_mfa_does_not_grant_company_access_to_client(self):
        self.user.is_staff = False
        self.user.save()
        device = self.device()
        self.client.post(self.verify_url, {'code': self.code(device)})
        response = self.client.get('/operations/')
        self.assertEqual(response.status_code, 403)
