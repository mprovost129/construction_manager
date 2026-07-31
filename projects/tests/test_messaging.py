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
from projects.tests import grant_internal_access


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
        grant_internal_access(cls.staff_user, cls.project, cls.other_project)
        grant_internal_access(
            cls.accountant,
            cls.project,
            cls.other_project,
            can_manage=False,
            can_invite_clients=False,
        )

    def create_thread(
        self,
        author=None,
        status=ConversationThread.Status.OPEN,
        subject='Cabinet selection question',
        body='Which cabinet finish would you prefer?',
        project=None,
    ):
        author = author or self.admin_user
        thread = ConversationThread.objects.create(
            project=project or self.project,
            subject=subject,
            status=status,
            created_by=author,
        )
        ConversationMessage.objects.create(
            thread=thread,
            author=author,
            body=body,
        )
        return thread

    def test_conversation_list_searches_subject_message_and_participant(self):
        target = self.create_thread(
            author=self.client_user,
            subject='Exterior color decision',
            body='We prefer evergreen shutters.',
        )
        ConversationMessage.objects.create(
            thread=target,
            author=self.admin_user,
            body='Evergreen has been confirmed with the supplier.',
        )
        self.create_thread()
        self.client.force_login(self.staff_user)
        url = reverse('projects:message_list', args=(self.project.pk,))

        for search in ('Exterior color', 'confirmed with', 'client@example.com'):
            with self.subTest(search=search):
                response = self.client.get(url, {'q': search})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(list(response.context['threads']), [target])
                self.assertEqual(response.context['threads'][0].message_count, 2)
                self.assertContains(response, 'Evergreen has been confirmed')

    def test_conversation_list_filters_status_and_ignores_unknown_status(self):
        open_thread = self.create_thread(subject='Open question')
        closed_thread = self.create_thread(
            status=ConversationThread.Status.CLOSED,
            subject='Closed question',
        )
        self.client.force_login(self.client_user)
        url = reverse('projects:message_list', args=(self.project.pk,))

        response = self.client.get(url, {'status': ConversationThread.Status.CLOSED})
        self.assertEqual(list(response.context['threads']), [closed_thread])
        self.assertTrue(response.context['has_message_filters'])

        response = self.client.get(url, {'status': 'not-a-status'})
        self.assertCountEqual(
            response.context['threads'],
            [open_thread, closed_thread],
        )
        self.assertEqual(response.context['message_status'], '')
        self.assertFalse(response.context['has_message_filters'])

    def test_conversation_search_never_leaks_another_project(self):
        self.create_thread(
            project=self.other_project,
            subject='Private project matter',
            body='The confidential phrase is blue-spruce.',
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse('projects:message_list', args=(self.project.pk,)),
            {'q': 'blue-spruce'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['threads']), [])
        self.assertContains(response, 'No conversations match these filters')

    def test_conversation_list_paginates_and_keeps_filters(self):
        for number in range(21):
            self.create_thread(subject=f'Permit item {number:02d}')
        self.client.force_login(self.staff_user)
        url = reverse('projects:message_list', args=(self.project.pk,))

        first_page = self.client.get(
            url,
            {'q': 'Permit', 'status': ConversationThread.Status.OPEN},
        )
        self.assertEqual(len(first_page.context['threads']), 20)
        self.assertContains(first_page, 'Page 1 of 2')
        self.assertContains(first_page, 'q=Permit&amp;status=open&amp;page=2')

        second_page = self.client.get(
            url,
            {
                'q': 'Permit',
                'status': ConversationThread.Status.OPEN,
                'page': 2,
            },
        )
        self.assertEqual(len(second_page.context['threads']), 1)

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
        close_url = reverse(
            'projects:message_status',
            args=(self.project.pk, thread.pk, 'close'),
        )
        reopen_url = reverse(
            'projects:message_status',
            args=(self.project.pk, thread.pk, 'reopen'),
        )

        self.client.post(close_url)
        duplicate_close = self.client.post(close_url, follow=True)
        thread.refresh_from_db()
        self.assertEqual(thread.status, ConversationThread.Status.CLOSED)
        self.assertContains(duplicate_close, 'Conversation is already closed.')
        self.assertEqual(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.MESSAGE_THREAD_STATUS_CHANGED
            ).count(),
            1,
        )

        self.client.post(reopen_url)
        duplicate_reopen = self.client.post(reopen_url, follow=True)
        thread.refresh_from_db()
        self.assertEqual(thread.status, ConversationThread.Status.OPEN)
        self.assertContains(duplicate_reopen, 'Conversation is already reopened.')
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
