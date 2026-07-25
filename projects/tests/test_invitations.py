from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from projects.models import (
    Organization,
    OrganizationMembership,
    Project,
    ProjectInvitation,
    ProjectMembership,
)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ProjectInvitationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(
            name='Example Builders',
            slug='example-builders',
        )
        cls.project = Project.objects.create(
            organization=cls.organization,
            name='Oak Street',
            status=Project.Status.ACTIVE,
        )
        cls.other_project = Project.objects.create(
            organization=cls.organization,
            name='Pine Street',
        )
        cls.admin_user = get_user_model().objects.create_user(
            'admin@example.com',
            'password',
        )
        cls.staff_user = get_user_model().objects.create_user(
            'staff@example.com',
            'password',
        )
        cls.accountant = get_user_model().objects.create_user(
            'accountant@example.com',
            'password',
        )
        cls.unrelated_user = get_user_model().objects.create_user(
            'unrelated@example.com',
            'password',
        )
        for user, role in (
            (cls.admin_user, OrganizationMembership.Role.ADMIN),
            (cls.staff_user, OrganizationMembership.Role.STAFF),
            (cls.accountant, OrganizationMembership.Role.ACCOUNTANT),
        ):
            OrganizationMembership.objects.create(
                organization=cls.organization,
                user=user,
                role=role,
            )

    def invite_url(self):
        return reverse('projects:invite_client', args=(self.project.pk,))

    def create_invitation(self, email='customer@example.com', **kwargs):
        return ProjectInvitation.objects.create(
            project=self.project,
            email=email,
            invited_by=self.admin_user,
            **kwargs,
        )

    def test_admin_can_send_project_specific_client_invitation(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            self.invite_url(),
            {'email': 'CUSTOMER@EXAMPLE.COM'},
            follow=True,
        )

        self.assertRedirects(
            response,
            reverse('projects:people', args=(self.project.pk,)),
        )
        invitation = ProjectInvitation.objects.get()
        self.assertEqual(invitation.email, 'customer@example.com')
        self.assertEqual(invitation.role, OrganizationMembership.Role.CLIENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(str(invitation.token), mail.outbox[0].body)
        self.assertIn(self.project.name, mail.outbox[0].subject)
        self.assertContains(response, 'Invitation sent to customer@example.com.')

    @patch('projects.services.send_mail', side_effect=RuntimeError('SMTP offline'))
    def test_email_failure_preserves_invitation_and_offers_resend(self, _send_mail):
        self.client.force_login(self.admin_user)

        with self.assertLogs('projects.services', level='ERROR'):
            response = self.client.post(
                self.invite_url(),
                {'email': 'customer@example.com'},
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            ProjectInvitation.objects.filter(email='customer@example.com').exists()
        )
        self.assertContains(
            response,
            'The invitation for customer@example.com was saved, but the email '
            'could not be sent. Use Resend to try again.',
        )
        self.assertNotContains(
            response,
            'Invitation sent to customer@example.com.',
        )

    def test_staff_can_send_client_invitation(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(self.invite_url(), {'email': 'client@example.com'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ProjectInvitation.objects.filter(email='client@example.com').exists())

    def test_accountant_cannot_send_client_invitation(self):
        self.client.force_login(self.accountant)
        response = self.client.get(self.invite_url())
        self.assertEqual(response.status_code, 403)

    def test_new_customer_accepts_invitation_and_is_logged_in(self):
        invitation = self.create_invitation()
        response = self.client.post(
            reverse('projects:accept_invitation', args=(invitation.token,)),
            {
                'first_name': 'Casey',
                'last_name': 'Customer',
                'password1': 'A-strong-test-password-923!',
                'password2': 'A-strong-test-password-923!',
            },
        )

        user = get_user_model().objects.get(email='customer@example.com')
        invitation.refresh_from_db()
        self.assertRedirects(
            response,
            reverse('projects:detail', args=(self.project.pk,)),
        )
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)
        self.assertEqual(invitation.accepted_by, user)
        self.assertIsNotNone(invitation.accepted_at)
        self.assertTrue(
            OrganizationMembership.objects.filter(
                organization=self.organization,
                user=user,
                role=OrganizationMembership.Role.CLIENT,
            ).exists()
        )
        self.assertTrue(
            ProjectMembership.objects.filter(
                project=self.project,
                user=user,
                role=OrganizationMembership.Role.CLIENT,
            ).exists()
        )
        self.assertFalse(
            ProjectMembership.objects.filter(project=self.other_project, user=user).exists()
        )

    def test_existing_customer_must_log_in_with_invited_email(self):
        user = get_user_model().objects.create_user(
            'customer@example.com',
            'password',
        )
        invitation = self.create_invitation(email=user.email)
        accept_url = reverse('projects:accept_invitation', args=(invitation.token,))

        anonymous_response = self.client.get(accept_url)
        self.assertContains(anonymous_response, 'Log in and continue')

        self.client.force_login(user)
        confirmation_response = self.client.get(accept_url)
        self.assertContains(confirmation_response, 'Accept project invitation')
        self.assertFalse(
            ProjectMembership.objects.filter(project=self.project, user=user).exists()
        )

        response = self.client.post(accept_url)
        self.assertRedirects(
            response,
            reverse('projects:detail', args=(self.project.pk,)),
        )
        self.assertTrue(
            ProjectMembership.objects.filter(project=self.project, user=user).exists()
        )

    def test_wrong_logged_in_email_cannot_accept_invitation(self):
        invitation = self.create_invitation()
        self.client.force_login(self.unrelated_user)
        response = self.client.get(
            reverse('projects:accept_invitation', args=(invitation.token,))
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProjectMembership.objects.exists())

    def test_expired_invitation_returns_gone(self):
        invitation = self.create_invitation(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        response = self.client.get(
            reverse('projects:accept_invitation', args=(invitation.token,))
        )
        self.assertEqual(response.status_code, 410)

    def test_contractor_can_replace_an_expired_invitation(self):
        expired = self.create_invitation(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        self.client.force_login(self.admin_user)
        response = self.client.post(
            self.invite_url(),
            {'email': expired.email},
        )

        self.assertEqual(response.status_code, 302)
        expired.refresh_from_db()
        self.assertIsNotNone(expired.revoked_at)
        self.assertEqual(
            ProjectInvitation.objects.filter(
                project=self.project,
                email=expired.email,
                revoked_at__isnull=True,
            ).count(),
            1,
        )
