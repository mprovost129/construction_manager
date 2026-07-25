from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from projects.models import (
    ActivityEvent,
    ConversationMessage,
    ConversationThread,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ProjectMessagingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(
            name='Example Builders', slug='example-builders'
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
        cls.subcontractor = get_user_model().objects.create_user(
            'sub@example.com', 'password'
        )
        for user, role in (
            (cls.admin_user, OrganizationMembership.Role.ADMIN),
            (cls.staff_user, OrganizationMembership.Role.STAFF),
            (cls.accountant, OrganizationMembership.Role.ACCOUNTANT),
            (cls.client_user, OrganizationMembership.Role.CLIENT),
            (cls.subcontractor, OrganizationMembership.Role.SUBCONTRACTOR),
        ):
            OrganizationMembership.objects.create(
                organization=cls.organization,
                user=user,
                role=role,
            )
        ProjectMembership.objects.create(
            project=cls.project,
            user=cls.client_user,
            role=OrganizationMembership.Role.CLIENT,
        )
        ProjectMembership.objects.create(
            project=cls.project,
            user=cls.subcontractor,
            role=OrganizationMembership.Role.SUBCONTRACTOR,
        )

    def create_thread(self, author=None, status=ConversationThread.Status.OPEN):
        author = author or self.admin_user
        thread = ConversationThread.objects.create(
            project=self.project,
            subject='Cabinet selection question',
            status=status,
            created_by=author,
        )
        ConversationMessage.objects.create(
            thread=thread,
            author=author,
            body='Which cabinet finish would you prefer?',
        )
        return thread

    def test_admin_starts_thread_and_only_clients_are_notified(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse('projects:message_create', args=(self.project.pk,)),
            {
                'subject': 'Cabinet selection question',
                'body': 'Which cabinet finish would you prefer?',
            },
        )

        thread = ConversationThread.objects.get()
        message = thread.messages.get()
        self.assertRedirects(
            response,
            reverse(
                'projects:message_thread', args=(self.project.pk, thread.pk)
            ),
        )
        self.assertEqual(message.author, self.admin_user)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['client@example.com'])
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.MESSAGE_THREAD_CREATED,
                project=self.project,
            ).exists()
        )

    @patch('projects.services.send_mail', side_effect=RuntimeError('SMTP offline'))
    def test_email_failure_does_not_lose_new_conversation(self, _send_mail):
        self.client.force_login(self.admin_user)

        with self.assertLogs('projects.services', level='ERROR'):
            response = self.client.post(
                reverse('projects:message_create', args=(self.project.pk,)),
                {
                    'subject': 'Delivery timing',
                    'body': 'When will the cabinets arrive?',
                },
                follow=True,
            )

        thread = ConversationThread.objects.get(subject='Delivery timing')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(thread.messages.count(), 1)
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.MESSAGE_THREAD_CREATED,
                project=self.project,
            ).exists()
        )
        self.assertContains(
            response,
            'Your update was saved, but the notification email could not be sent.',
        )

    def test_client_reply_notifies_admin_and_staff_but_not_accountant(self):
        thread = self.create_thread()
        self.client.force_login(self.client_user)
        response = self.client.post(
            reverse(
                'projects:message_thread', args=(self.project.pk, thread.pk)
            ),
            {'body': 'We prefer the natural oak finish.'},
        )

        self.assertEqual(response.status_code, 302)
        reply = thread.messages.order_by('-pk').first()
        self.assertEqual(reply.author, self.client_user)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].to,
            ['admin@example.com', 'staff@example.com'],
        )
        self.assertNotIn('accountant@example.com', mail.outbox[0].to)
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.MESSAGE_SENT,
                actor=self.client_user,
            ).exists()
        )

    def test_admin_staff_and_client_can_access_messaging(self):
        url = reverse('projects:message_list', args=(self.project.pk,))
        for user in (self.admin_user, self.staff_user, self.client_user):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_accountant_and_subcontractor_cannot_access_messaging(self):
        url = reverse('projects:message_list', args=(self.project.pk,))
        for user in (self.accountant, self.subcontractor):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_client_cannot_access_thread_on_unassigned_project(self):
        thread = ConversationThread.objects.create(
            project=self.other_project,
            subject='Private project question',
            created_by=self.admin_user,
        )
        self.client.force_login(self.client_user)
        response = self.client.get(
            reverse(
                'projects:message_thread', args=(self.other_project.pk, thread.pk)
            )
        )
        self.assertEqual(response.status_code, 404)

    def test_contractor_can_close_and_reopen_thread(self):
        thread = self.create_thread()
        self.client.force_login(self.staff_user)

        self.client.post(
            reverse(
                'projects:message_status',
                args=(self.project.pk, thread.pk, 'close'),
            )
        )
        thread.refresh_from_db()
        self.assertEqual(thread.status, ConversationThread.Status.CLOSED)

        self.client.post(
            reverse(
                'projects:message_status',
                args=(self.project.pk, thread.pk, 'reopen'),
            )
        )
        thread.refresh_from_db()
        self.assertEqual(thread.status, ConversationThread.Status.OPEN)
        self.assertEqual(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.MESSAGE_THREAD_STATUS_CHANGED
            ).count(),
            2,
        )

    def test_client_cannot_close_thread_or_reply_when_closed(self):
        thread = self.create_thread(status=ConversationThread.Status.CLOSED)
        self.client.force_login(self.client_user)

        close_response = self.client.post(
            reverse(
                'projects:message_status',
                args=(self.project.pk, thread.pk, 'reopen'),
            )
        )
        self.assertEqual(close_response.status_code, 403)

        reply_response = self.client.post(
            reverse(
                'projects:message_thread', args=(self.project.pk, thread.pk)
            ),
            {'body': 'This should not be saved.'},
        )
        self.assertEqual(reply_response.status_code, 302)
        self.assertEqual(thread.messages.count(), 1)
        self.assertEqual(len(mail.outbox), 0)
