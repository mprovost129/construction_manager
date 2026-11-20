import time

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django_otp.oath import totp

from projects.forms import TeamInvitationForm
from projects.models import ActivityEvent, Organization, OrganizationInvitation, OrganizationMembership
from projects.services import accept_organization_invitation
from users.mfa import enrollment_required, issue_recovery_codes
from users.models import AuthenticatorDevice, MFAAuditEvent, MFARecoveryCode


class OptionalMFATests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Example', slug='example')
        self.admin = get_user_model().objects.create_user('admin@example.test', 'Test-login-password-43!')
        self.person = get_user_model().objects.create_user('person@example.test', 'Person-login-password-63!')
        self.admin_member = OrganizationMembership.objects.create(organization=self.org, user=self.admin, role='admin')
        self.member = OrganizationMembership.objects.create(organization=self.org, user=self.person, role='accountant')
        self.update_url = reverse('projects:update_team_membership', args=['example', self.member.pk])
        self.own_url = reverse('projects:update_own_mfa_requirement', args=['example'])
        self.disable_url = reverse('users:mfa_disable')
        self.security_url = reverse('users:security')
        self.client.force_login(self.admin)

    def verified_device(self, user=None):
        user = user or self.admin
        device = AuthenticatorDevice.objects.create(account=user, confirmed=True)
        session = self.client.session
        session.update({'mfa_user': user.pk, 'mfa_device': device.pk, 'mfa_at': time.time()})
        session.save()
        return device

    def disable_data(self, authenticator, **changes):
        data = {'password': 'Test-login-password-43!', 'code': f'{totp(authenticator.bin_key):06d}', 'confirm': 'on'}
        data.update(changes)
        return data

    @override_settings(MFA_REQUIRED_FOR_ADMINS=True)
    def test_default_optional_including_staff_superuser_and_financial_roles(self):
        # The obsolete environment switch must not silently override the checkbox.
        for role in ('admin', 'accountant', 'manager'):
            OrganizationMembership.objects.filter(pk=self.admin_member.pk).update(role=role)
            self.assertFalse(enrollment_required(self.admin))
            self.assertEqual(self.client.get('/').status_code, 200)
        self.admin.is_staff = self.admin.is_superuser = True
        self.admin.save()
        self.assertFalse(enrollment_required(self.admin))
        self.assertContains(self.client.get(self.security_url), 'optional for your account')

    def test_admin_checkbox_enforces_next_request_and_revocation_makes_optional(self):
        self.client.post(self.update_url, {'role': 'accountant', 'is_active': 'on', 'mfa_required': 'on'})
        self.member.refresh_from_db()
        self.assertTrue(self.member.mfa_required)
        self.assertTrue(ActivityEvent.objects.filter(metadata__after__mfa_required=True).exists())
        member_client = Client()
        member_client.force_login(self.person)
        self.assertTrue(member_client.get('/').url.startswith(reverse('users:mfa_setup')))
        self.assertEqual(member_client.post('/projects/1/').status_code, 403)
        self.client.post(self.update_url, {'role': 'accountant', 'is_active': 'on'})
        self.assertEqual(member_client.get('/').status_code, 200)

    def test_unchecking_requirement_does_not_bypass_existing_authenticator(self):
        AuthenticatorDevice.objects.create(account=self.person, confirmed=True)
        self.client.force_login(self.person)
        self.assertTrue(self.client.get('/').url.startswith(reverse('users:mfa_verify')))

    def test_non_admin_and_foreign_admin_cannot_change_policy(self):
        self.client.force_login(self.person)
        self.assertEqual(self.client.post(self.update_url, {'mfa_required': 'on'}).status_code, 403)
        self.assertEqual(self.client.post(self.own_url, {'mfa_required': 'on'}).status_code, 403)
        other = Organization.objects.create(name='Other', slug='other')
        OrganizationMembership.objects.create(organization=other, user=self.person, role='admin')
        self.assertEqual(self.client.post(self.own_url, {'mfa_required': 'on'}).status_code, 403)
        self.member.refresh_from_db()
        self.assertFalse(self.member.mfa_required)

    def test_any_active_internal_company_requirement_applies(self):
        other = Organization.objects.create(name='Other', slug='other')
        member = OrganizationMembership.objects.create(organization=other, user=self.admin, role='staff', mfa_required=True)
        self.assertTrue(enrollment_required(self.admin))
        member.is_active = False
        member.save()
        self.assertFalse(enrollment_required(self.admin))
        member.is_active = True
        member.role = 'client'
        member.save()
        self.assertFalse(enrollment_required(self.admin))

    def test_own_policy_form_cannot_alter_role_or_access(self):
        self.assertEqual(self.client.post(self.own_url, {'mfa_required': 'on', 'role': 'staff',
            'accounting_view': '', 'user': self.person.pk}).status_code, 302)
        self.admin_member.refresh_from_db()
        self.assertTrue(self.admin_member.mfa_required)
        self.assertEqual(self.admin_member.role, 'admin')
        self.assertIsNone(self.admin_member.accounting_view)
        self.assertTrue(self.client.get('/').url.startswith(reverse('users:mfa_setup')))
        self.verified_device()
        self.assertEqual(self.client.post(self.own_url, {}).status_code, 302)
        self.assertFalse(enrollment_required(self.admin))

    def test_invite_default_optional_and_resend_acceptance_preserve_requirement(self):
        form = TeamInvitationForm(organization=self.org)
        self.assertFalse(form['mfa_required'].value())
        invitation = OrganizationInvitation.objects.create(organization=self.org, invited_by=self.admin,
            email=self.person.email, role='accountant', mfa_required=True)
        self.client.post(reverse('projects:resend_team_invitation', args=['example', invitation.pk]))
        replacement = OrganizationInvitation.objects.get(revoked_at__isnull=True)
        self.assertTrue(replacement.mfa_required)
        accept_organization_invitation(replacement, self.person)
        self.assertTrue(enrollment_required(self.person))

    def test_optional_owner_can_disable_and_recovery_codes_are_invalidated(self):
        device = self.verified_device()
        issue_recovery_codes(device)
        self.assertContains(self.client.get(self.security_url), 'Turn off two-step verification')
        old_session = self.client.session.session_key
        self.assertRedirects(self.client.post(self.disable_url, self.disable_data(device)), self.security_url)
        self.assertFalse(AuthenticatorDevice.objects.exists())
        self.assertFalse(MFARecoveryCode.objects.exists())
        self.assertNotIn('mfa_device', self.client.session)
        self.assertNotEqual(old_session, self.client.session.session_key)
        self.assertTrue(MFAAuditEvent.objects.filter(user=self.admin, event='disabled_by_owner').exists())
        self.assertContains(self.client.get(self.security_url), 'Set up two-step verification')

    def test_disable_requires_password_fresh_code_and_confirmation(self):
        device = self.verified_device()
        for changes in ({'password': 'wrong'}, {'confirm': ''}, {'code': 'invalid'}):
            self.assertEqual(self.client.post(self.disable_url, self.disable_data(device, **changes)).status_code, 200)
            self.assertTrue(AuthenticatorDevice.objects.filter(pk=device.pk).exists())
        self.assertFalse(MFAAuditEvent.objects.filter(event='disabled_by_owner').exists())

    def test_disable_accepts_one_recovery_code_and_ignores_foreign_account(self):
        device = self.verified_device()
        foreign = AuthenticatorDevice.objects.create(account=self.person, confirmed=True)
        code = issue_recovery_codes(device)[0]
        response = self.client.post(self.disable_url, self.disable_data(device, code=code,
            use_recovery='on', user=self.person.pk, device=foreign.pk))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AuthenticatorDevice.objects.filter(pk=foreign.pk).exists())
        self.assertFalse(AuthenticatorDevice.objects.filter(pk=device.pk).exists())

    def test_required_user_cannot_disable_even_with_valid_credentials(self):
        device = self.verified_device()
        self.admin_member.mfa_required = True
        self.admin_member.save()
        self.assertNotContains(self.client.get(self.security_url), 'Turn off two-step verification')
        self.assertEqual(self.client.get(self.disable_url).status_code, 403)
        self.assertEqual(self.client.post(self.disable_url, self.disable_data(device)).status_code, 403)
        self.assertTrue(AuthenticatorDevice.objects.exists())

    def test_policy_and_disable_retain_csrf_turnstile_and_verified_session(self):
        device = self.verified_device()
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.admin)
        self.assertEqual(csrf.post(self.own_url, {'mfa_required': 'on'}).status_code, 403)
        self.assertEqual(csrf.post(self.disable_url, self.disable_data(device)).status_code, 403)
        with override_settings(TURNSTILE_FORMS_ENABLED=True):
            self.assertEqual(self.client.post(self.own_url, {}).status_code, 403)
            self.assertEqual(self.client.post(self.disable_url, self.disable_data(device)).status_code, 403)
        unverified = Client()
        unverified.force_login(self.admin)
        self.assertEqual(unverified.post(self.disable_url, self.disable_data(device)).status_code, 403)
        self.assertTrue(AuthenticatorDevice.objects.exists())
