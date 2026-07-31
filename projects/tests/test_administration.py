from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from projects.models import (
    ActivityEvent,
    Organization,
    OrganizationMembership,
    Project,
    ProjectInternalAccess,
    ProjectInvitation,
    ProjectMembership,
)
from projects.tests import grant_internal_access


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ProjectAdministrationTests(TestCase):
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
        cls.accountant = get_user_model().objects.create_user(
            'accountant@example.com', 'password'
        )
        cls.client_user = get_user_model().objects.create_user(
            'client@example.com', 'password'
        )
        for user, role in (
            (cls.admin_user, OrganizationMembership.Role.ADMIN),
            (cls.staff_user, OrganizationMembership.Role.STAFF),
            (cls.accountant, OrganizationMembership.Role.ACCOUNTANT),
            (cls.client_user, OrganizationMembership.Role.CLIENT),
        ):
            OrganizationMembership.objects.create(
                organization=cls.organization, user=user, role=role
            )
        cls.project = Project.objects.create(
            organization=cls.organization,
            name='Oak Street',
            status=Project.Status.ACTIVE,
            created_by=cls.admin_user,
        )
        cls.client_membership = ProjectMembership.objects.create(
            project=cls.project,
            user=cls.client_user,
            role=OrganizationMembership.Role.CLIENT,
            invited_by=cls.admin_user,
        )
        grant_internal_access(cls.staff_user, cls.project)
        grant_internal_access(
            cls.accountant,
            cls.project,
            can_manage=False,
            can_invite_clients=False,
        )

    def project_payload(self, name='Pine Street'):
        return {
            'organization': self.organization.pk,
            'name': name,
            'code': 'PINE-01',
            'description': 'A new residence.',
            'status': Project.Status.PLANNING,
            'start_date': '',
            'target_completion_date': '',
        }

    def test_admin_can_create_project_and_audit_event(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(reverse('projects:create'), self.project_payload())

        project = Project.objects.get(name='Pine Street')
        self.assertRedirects(response, reverse('projects:detail', args=(project.pk,)))
        self.assertEqual(project.created_by, self.admin_user)
        self.assertTrue(
            ActivityEvent.objects.filter(
                project=project,
                event_type=ActivityEvent.Type.PROJECT_CREATED,
                actor=self.admin_user,
            ).exists()
        )

    def test_staff_can_update_project_and_changed_fields_are_audited(self):
        self.client.force_login(self.staff_user)
        payload = self.project_payload(name='Oak Street Updated')
        payload['status'] = Project.Status.ON_HOLD
        response = self.client.post(
            reverse('projects:edit', args=(self.project.pk,)), payload
        )

        self.assertRedirects(
            response, reverse('projects:detail', args=(self.project.pk,))
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Oak Street Updated')
        event = ActivityEvent.objects.get(event_type=ActivityEvent.Type.PROJECT_UPDATED)
        self.assertIn('name', event.metadata['fields'])
        self.assertIn('status', event.metadata['fields'])

    def test_accountant_cannot_create_or_edit_projects(self):
        self.client.force_login(self.accountant)
        self.assertEqual(self.client.get(reverse('projects:create')).status_code, 403)
        self.assertEqual(
            self.client.get(reverse('projects:edit', args=(self.project.pk,))).status_code,
            403,
        )

    def test_client_has_no_administration_navigation(self):
        self.client.force_login(self.client_user)
        home = self.client.get(reverse('core:home'))
        project = self.client.get(reverse('projects:detail', args=(self.project.pk,)))

        self.assertNotContains(home, 'New project')
        self.assertNotContains(home, '>Company<', html=False)
        self.assertNotContains(project, 'People &amp; access', html=True)
        self.assertNotContains(project, 'Recent activity')

    def test_staff_can_revoke_and_restore_client_project_access(self):
        self.client.force_login(self.staff_user)
        revoke_url = reverse(
            'projects:project_member_access',
            args=(self.project.pk, self.client_membership.pk, 'revoke'),
        )
        restore_url = reverse(
            'projects:project_member_access',
            args=(self.project.pk, self.client_membership.pk, 'restore'),
        )

        self.client.post(revoke_url)
        duplicate_revoke = self.client.post(revoke_url, follow=True)
        self.client_membership.refresh_from_db()
        self.assertFalse(self.client_membership.is_active)
        self.assertContains(duplicate_revoke, 'Project access is already revoked.')
        self.assertEqual(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.CLIENT_ACCESS_REVOKED
            ).count(),
            1,
        )

        self.client.post(restore_url)
        duplicate_restore = self.client.post(restore_url, follow=True)
        self.client_membership.refresh_from_db()
        self.assertTrue(self.client_membership.is_active)
        self.assertContains(duplicate_restore, 'Project access is already active.')
        self.assertEqual(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.CLIENT_ACCESS_RESTORED
            ).count(),
            1,
        )

    def test_admin_assigns_and_revokes_internal_project_access(self):
        manager = get_user_model().objects.create_user(
            'manager@example.com', 'password'
        )
        membership = OrganizationMembership.objects.create(
            organization=self.organization,
            user=manager,
            role=OrganizationMembership.Role.MANAGER,
        )
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse('projects:internal_access_update', args=(self.project.pk,)),
            {
                'membership': membership.pk,
                'can_manage': 'on',
                'can_invite_clients': 'on',
                'receives_notifications': 'on',
            },
        )

        access = ProjectInternalAccess.objects.get(
            project=self.project,
            membership=membership,
        )
        self.assertRedirects(response, reverse('projects:people', args=(self.project.pk,)))
        self.assertTrue(access.can_manage)
        self.assertTrue(access.can_invite_clients)
        self.assertTrue(access.receives_notifications)
        self.assertTrue(
            ActivityEvent.objects.filter(
                project=self.project,
                event_type=ActivityEvent.Type.INTERNAL_ACCESS_ASSIGNED,
                actor=self.admin_user,
            ).exists()
        )

        revoke_response = self.client.post(
            reverse(
                'projects:internal_access_revoke',
                args=(self.project.pk, access.pk),
            )
        )
        access.refresh_from_db()
        self.assertRedirects(
            revoke_response,
            reverse('projects:people', args=(self.project.pk,)),
        )
        self.assertFalse(access.is_active)
        self.assertTrue(
            ActivityEvent.objects.filter(
                project=self.project,
                event_type=ActivityEvent.Type.INTERNAL_ACCESS_REVOKED,
                actor=self.admin_user,
            ).exists()
        )

    def test_staff_can_resend_and_revoke_customer_invitation(self):
        invitation = ProjectInvitation.objects.create(
            project=self.project,
            email='pending@example.com',
            invited_by=self.admin_user,
        )
        self.client.force_login(self.staff_user)

        resend_response = self.client.post(
            reverse(
                'projects:resend_client_invitation',
                args=(self.project.pk, invitation.pk),
            )
        )
        self.assertEqual(resend_response.status_code, 302)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.revoked_at)
        replacement = ProjectInvitation.objects.get(
            project=self.project,
            email='pending@example.com',
            revoked_at__isnull=True,
        )
        self.assertEqual(len(mail.outbox), 1)

        self.client.post(
            reverse(
                'projects:revoke_client_invitation',
                args=(self.project.pk, replacement.pk),
            )
        )
        replacement.refresh_from_db()
        self.assertIsNotNone(replacement.revoked_at)
