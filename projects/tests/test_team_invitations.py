from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from projects.models import (
    ActivityEvent,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TeamInvitationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(
            name='Example Builders', slug='example-builders'
        )
        cls.admin_user = get_user_model().objects.create_user(
            'admin@example.com', 'password'
        )
        cls.staff_user = get_user_model().objects.create_user(
            'staff@example.com', 'password'
        )
        cls.other_user = get_user_model().objects.create_user(
            'other@example.com', 'password'
        )
        cls.admin_membership = OrganizationMembership.objects.create(
            organization=cls.organization,
            user=cls.admin_user,
            role=OrganizationMembership.Role.ADMIN,
        )
        cls.staff_membership = OrganizationMembership.objects.create(
            organization=cls.organization,
            user=cls.staff_user,
            role=OrganizationMembership.Role.STAFF,
        )

    def invite_url(self):
        return reverse(
            'projects:invite_team_member', args=(self.organization.slug,)
        )

    def create_invitation(self, email='newstaff@example.com'):
        return OrganizationInvitation.objects.create(
            organization=self.organization,
            email=email,
            role=OrganizationMembership.Role.STAFF,
            invited_by=self.admin_user,
        )

    def test_admin_can_invite_team_member(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            self.invite_url(),
            {'email': 'NEWSTAFF@EXAMPLE.COM', 'role': 'staff'},
        )

        self.assertRedirects(
            response,
            reverse('projects:company_team', args=(self.organization.slug,)),
        )
        invitation = OrganizationInvitation.objects.get()
        self.assertEqual(invitation.email, 'newstaff@example.com')
        self.assertIn(str(invitation.token), mail.outbox[0].body)
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.TEAM_INVITED
            ).exists()
        )

    def test_staff_can_view_team_but_cannot_manage_it(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(
            reverse('projects:company_team', args=(self.organization.slug,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Invite team member')
        self.assertEqual(self.client.get(self.invite_url()).status_code, 403)

    def test_new_user_accepts_team_invitation(self):
        invitation = self.create_invitation()
        response = self.client.post(
            reverse('projects:accept_team_invitation', args=(invitation.token,)),
            {
                'first_name': 'Taylor',
                'last_name': 'Team',
                'password1': 'A-strong-test-password-923!',
                'password2': 'A-strong-test-password-923!',
            },
        )

        user = get_user_model().objects.get(email='newstaff@example.com')
        membership = OrganizationMembership.objects.get(
            organization=self.organization,
            user=user,
        )
        self.assertRedirects(
            response,
            reverse('projects:company_team', args=(self.organization.slug,)),
        )
        self.assertEqual(membership.role, OrganizationMembership.Role.STAFF)
        self.assertTrue(membership.is_active)
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.TEAM_JOINED,
                actor=user,
            ).exists()
        )

    def test_wrong_logged_in_email_cannot_accept_team_invitation(self):
        invitation = self.create_invitation()
        self.client.force_login(self.other_user)
        response = self.client.get(
            reverse('projects:accept_team_invitation', args=(invitation.token,))
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_change_another_team_members_role_and_access(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse(
                'projects:update_team_membership',
                args=(self.organization.slug, self.staff_membership.pk),
            ),
            {'role': 'accountant'},
        )

        self.assertEqual(response.status_code, 302)
        self.staff_membership.refresh_from_db()
        self.assertEqual(
            self.staff_membership.role, OrganizationMembership.Role.ACCOUNTANT
        )
        self.assertFalse(self.staff_membership.is_active)
        self.assertEqual(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.TEAM_ROLE_CHANGED
            ).count(),
            1,
        )
        self.assertEqual(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.TEAM_ACCESS_CHANGED
            ).count(),
            1,
        )

    def test_admin_cannot_change_own_membership(self):
        self.client.force_login(self.admin_user)
        self.client.post(
            reverse(
                'projects:update_team_membership',
                args=(self.organization.slug, self.admin_membership.pk),
            ),
            {'role': 'staff', 'is_active': 'on'},
        )
        self.admin_membership.refresh_from_db()
        self.assertEqual(self.admin_membership.role, OrganizationMembership.Role.ADMIN)
        self.assertTrue(self.admin_membership.is_active)

    def test_admin_can_resend_and_revoke_team_invitation(self):
        invitation = self.create_invitation()
        self.client.force_login(self.admin_user)
        self.client.post(
            reverse(
                'projects:resend_team_invitation',
                args=(self.organization.slug, invitation.pk),
            )
        )
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.revoked_at)
        replacement = OrganizationInvitation.objects.get(
            organization=self.organization,
            email=invitation.email,
            revoked_at__isnull=True,
        )

        self.client.post(
            reverse(
                'projects:revoke_team_invitation',
                args=(self.organization.slug, replacement.pk),
            )
        )
        replacement.refresh_from_db()
        self.assertIsNotNone(replacement.revoked_at)
