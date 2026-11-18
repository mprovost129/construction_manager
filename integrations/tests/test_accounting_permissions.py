from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from integrations.models import QuickBooksDesktopOnboarding
from projects.access import can_view_project_budget
from projects.forms import TeamMembershipForm
from projects.models import Organization, OrganizationInvitation, OrganizationMembership, Project, ActivityEvent
from projects.services import accept_organization_invitation
from projects.tests import grant_internal_access


@override_settings(QUICKBOOKS_INTEGRATION_ENABLED=False)
class AccountingPermissionsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Heritage', slug='heritage')
        self.other = Organization.objects.create(name='Other', slug='other')
        self.admin = get_user_model().objects.create_user('owner@example.com')
        self.person = get_user_model().objects.create_user('accountant@example.com')
        self.admin_member = OrganizationMembership.objects.create(organization=self.org, user=self.admin, role='admin')
        self.member = OrganizationMembership.objects.create(organization=self.org, user=self.person, role='accountant')
        self.url = reverse('integrations:quickbooks_setup')
        self.team_url = reverse('projects:company_team', args=[self.org.slug])
        self.update_url = reverse('projects:update_team_membership', args=[self.org.slug, self.member.pk])
        self.client.force_login(self.person)

    def grant(self, **values):
        for name, value in values.items():
            setattr(self.member, name, value)
        self.member.save()

    def test_read_only_and_revoked_access_enforced_on_direct_requests(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.grant(accounting_view=True)
        page = self.client.get(self.url)
        self.assertContains(page, 'Your access is read-only.')
        self.assertNotContains(page, 'type="submit" class="btn btn-primary">Save setup details')
        self.assertEqual(self.client.post(self.url, {'organization': 'heritage'}).status_code, 403)
        self.grant(accounting_edit=True)
        self.assertEqual(self.client.post(self.url, {'organization': 'heritage', 'hosting': 'office'}).status_code, 302)
        self.grant(accounting_view=False)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {'organization': 'heritage'}).status_code, 403)
        self.assertNotContains(self.client.get('/'), '>QuickBooks</a>')

    def test_clients_and_inactive_members_cannot_use_stored_permissions(self):
        self.grant(accounting_view=True, accounting_edit=True, role='client')
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.grant(role='accountant', is_active=False)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_delegation_is_company_scoped_and_does_not_grant_admin_controls(self):
        self.grant(accounting_view=True, accounting_edit=True)
        self.assertEqual(self.client.get(self.url + '?organization=other').status_code, 404)
        self.assertEqual(self.client.post(self.url, {'organization': 'other'}).status_code, 404)
        self.assertEqual(self.client.post(self.update_url, {'role': 'admin', 'is_active': 'on'}).status_code, 403)
        self.assertNotContains(self.client.get(self.url), 'Invite accountant or setup person')

    def test_admin_checkbox_permissions_are_explicit_and_audited(self):
        self.client.force_login(self.admin)
        self.client.post(self.update_url, {'role': 'admin', 'is_active': 'on', 'accounting_view': 'on'})
        self.member.refresh_from_db()
        self.assertTrue(self.member.can_view_accounting)
        self.assertFalse(self.member.can_edit_accounting)
        self.assertTrue(ActivityEvent.objects.filter(event_type='team_access_changed').exists())
        self.client.force_login(self.person)
        self.assertEqual(self.client.post(self.url, {'organization': 'heritage'}).status_code, 403)

    def test_invalid_edit_without_view_does_not_save(self):
        self.client.force_login(self.admin)
        self.client.post(self.update_url, {'role': 'accountant', 'is_active': 'on', 'accounting_edit': 'on'})
        self.member.refresh_from_db()
        self.assertIsNone(self.member.accounting_edit)

    def test_existing_role_defaults_render_as_checkboxes_and_can_be_revoked(self):
        form = TeamMembershipForm(instance=self.admin_member)
        self.assertTrue(form['accounting_view'].value())
        self.assertTrue(form['project_budget_view'].value())
        self.admin_member.accounting_view = False
        self.admin_member.save()
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_invitation_preserves_grants_and_resend_does_not_expand_them(self):
        invitation = OrganizationInvitation.objects.create(organization=self.org, email=self.person.email,
            role='accountant', invited_by=self.admin, accounting_view=True, accounting_edit=False,
            project_budget_view=False, desktop_manage=True, desktop_check=False)
        self.client.force_login(self.admin)
        self.client.post(reverse('projects:resend_team_invitation', args=[self.org.slug, invitation.pk]))
        replacement = OrganizationInvitation.objects.get(revoked_at__isnull=True)
        accept_organization_invitation(replacement, self.person)
        self.member.refresh_from_db()
        self.assertTrue(self.member.can_view_accounting)
        self.assertFalse(self.member.can_edit_accounting)
        self.assertFalse(self.member.can_view_budget)
        self.assertTrue(self.member.can_manage_desktop)
        self.assertFalse(self.member.can_check_desktop)

    def test_accountant_invite_preset_and_acceptance_landing(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('projects:invite_team_member', args=['heritage']) + '?purpose=accounting')
        self.assertEqual(response.context['form']['role'].value(), 'accountant')
        self.assertTrue(response.context['form']['accounting_edit'].value())
        self.assertFalse(response.context['form']['project_budget_view'].value())
        invitation = OrganizationInvitation.objects.create(organization=self.org, email=self.person.email,
            role='accountant', invited_by=self.admin, accounting_view=True, accounting_edit=True)
        self.client.force_login(self.person)
        response = self.client.post(reverse('projects:accept_team_invitation', args=[invitation.token]))
        self.assertEqual(response.url, self.url + '?organization=heritage')

    def test_budget_checkbox_still_requires_project_assignment(self):
        project = Project.objects.create(organization=self.org, name='Assigned only')
        self.grant(project_budget_view=True)
        self.assertFalse(can_view_project_budget(self.person, project))
        grant_internal_access(self.person, project)
        self.assertTrue(can_view_project_budget(self.person, project))
        self.grant(project_budget_view=False)
        self.assertFalse(can_view_project_budget(self.person, project))

    def test_stale_shared_setup_form_cannot_overwrite_newer_changes(self):
        self.grant(accounting_view=True, accounting_edit=True)
        first = self.client.get(self.url)
        self.assertEqual(first.context['form']['revision'].value(), 0)
        self.client.post(self.url, {'organization': 'heritage', 'revision': 0, 'workstation': 'First save'})
        response = self.client.post(self.url, {'organization': 'heritage', 'revision': 0, 'workstation': 'Stale save'})
        self.assertContains(response, 'Someone else updated this setup.')
        saved = QuickBooksDesktopOnboarding.objects.get()
        self.assertEqual(saved.workstation, 'First save')
        self.assertEqual(saved.revision, 1)
        self.assertEqual(ActivityEvent.objects.filter(event_type='accounting_setup_updated').count(), 1)

    @override_settings(MFA_REQUIRED_FOR_ADMINS=True)
    def test_delegated_accounting_requires_mfa_before_read_or_write(self):
        self.grant(accounting_view=True, accounting_edit=True, project_budget_view=False)
        response = self.client.get(self.url)
        self.assertTrue(response.url.startswith(reverse('users:mfa_setup')))
        self.assertEqual(self.client.post(self.url, {'organization': 'heritage'}).status_code, 403)
        self.assertFalse(QuickBooksDesktopOnboarding.objects.exists())

    @override_settings(TURNSTILE_FORMS_ENABLED=True, TURNSTILE_SITE_KEY='test-site',
                       TURNSTILE_SECRET_KEY='test-secret')
    def test_missing_captcha_blocks_setup_and_permission_changes(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.update_url, {'role': 'admin', 'is_active': 'on'}).status_code, 403)
        self.assertEqual(self.client.post(self.url, {'organization': 'heritage'}).status_code, 403)
        self.member.refresh_from_db()
        self.assertEqual(self.member.role, 'accountant')
        self.assertFalse(QuickBooksDesktopOnboarding.objects.exists())
