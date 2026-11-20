import io
import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from projects.models import (
    ActivityEvent,
    InAppNotification,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
    StaffMessage,
    StaffMessageDelivery,
    StaffParticipant,
    StaffThread,
)
from projects.staff_access import staff_threads, visible_notifications
from projects.staff_messaging import process_email_queue
from projects.tests import grant_internal_access


class StaffMessagingTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Test Heritage', slug='test-heritage')
        self.other_org = Organization.objects.create(name='Other Builder', slug='other-builder')
        self.project = Project.objects.create(organization=self.org, name='Test House')
        self.other_project = Project.objects.create(organization=self.other_org, name='Other House')
        self.sender = self.user('sender', 'staff')
        self.receiver = self.user('receiver', 'staff')
        self.admin = self.user('admin', 'admin')
        self.customer = self.user('client', 'client')
        self.subcontractor = self.user('sub', 'subcontractor')
        self.unassigned = self.user('unassigned', 'staff')
        self.outsider = self.user('outsider', 'admin', org=self.other_org)
        grant_internal_access(self.sender, self.project, can_manage=False)
        grant_internal_access(self.receiver, self.project, can_manage=False)
        ProjectMembership.objects.create(project=self.project, user=self.customer, role='client')
        self.client.force_login(self.sender)

    def user(self, name, role, org=None):
        user = get_user_model().objects.create_user(f'{name}@example.com', 'test-only')
        OrganizationMembership.objects.create(organization=org or self.org, user=user, role=role)
        return user

    def compose_url(self, project=False):
        return reverse('projects:staff_message_compose') + (f'?project={self.project.pk}' if project else f'?organization={self.org.pk}')

    def data(self, **overrides):
        data = {'recipients': [self.receiver.pk], 'subject': 'PRIVATE STAFF SUBJECT',
                'body': 'Internal question about next week.\nPlease confirm the plan.',
                'submission_id': str(uuid.uuid4())}
        data.update(overrides)
        return data

    def create_thread(self, project=False, **overrides):
        response = self.client.post(self.compose_url(project), self.data(**overrides))
        self.assertEqual(response.status_code, 302)
        return StaffThread.objects.latest('pk')

    def detail_url(self, thread):
        return reverse('projects:staff_message_thread', args=[thread.pk])

    def test_general_conversation_is_project_independent_with_immediate_alert(self):
        thread = self.create_thread()
        self.assertIsNone(thread.project_id)
        self.assertEqual(thread.participants.count(), 2)
        self.assertEqual(thread.messages.count(), 1)
        self.assertEqual(StaffMessageDelivery.objects.get().email_status, 'pending')
        notification = InAppNotification.objects.get()
        self.assertEqual(notification.user, self.receiver)
        self.assertIsNone(notification.project_id)
        self.assertEqual(notification.staff_thread, thread)
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(self.client.get(self.detail_url(thread)), 'General conversation')

    def test_project_conversation_is_linked_and_shown_in_project_inbox(self):
        thread = self.create_thread(project=True)
        self.assertEqual(thread.project, self.project)
        self.assertContains(self.client.get(reverse('projects:staff_message_inbox') + f'?project={self.project.pk}'), thread.subject)
        self.assertNotContains(self.client.get(reverse('projects:staff_message_inbox') + '?scope=general'), thread.subject)
        self.assertEqual(InAppNotification.objects.get().project, self.project)

    def test_all_internal_roles_can_participate_in_general_conversations(self):
        for role in OrganizationMembership.INTERNAL_ROLES:
            membership = self.sender.organization_memberships.get()
            membership.role = role
            membership.save()
            self.assertContains(self.client.get(reverse('projects:staff_message_inbox')), 'New conversation')

    def test_client_and_subcontractor_cannot_enter_or_receive_staff_messages(self):
        thread = self.create_thread(project=True)
        for user in (self.customer, self.subcontractor):
            self.client.force_login(user)
            for url in (reverse('projects:staff_message_inbox'), reverse('projects:staff_message_start'), self.detail_url(thread), self.compose_url()):
                self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(self.detail_url(thread), self.data()).status_code, 403)
            self.assertNotContains(self.client.get(reverse('core:home')), '>Staff messages</a>')
        self.client.force_login(self.sender)
        for user in (self.customer, self.subcontractor, self.outsider):
            response = self.client.post(self.compose_url(), self.data(recipients=[user.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertIn('recipients', response.context['form'].errors)

    def test_nonparticipants_including_admins_cannot_read_search_or_reply(self):
        thread = self.create_thread()
        for user in (self.admin, self.unassigned, self.outsider):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.detail_url(thread)).status_code, 404)
            self.assertEqual(self.client.post(self.detail_url(thread), self.data()).status_code, 404)
            self.assertNotContains(self.client.get(reverse('projects:staff_message_inbox') + '?q=PRIVATE'), thread.subject)

    def test_project_recipients_require_internal_project_access(self):
        response = self.client.post(self.compose_url(True), self.data(recipients=[self.unassigned.pk]))
        self.assertIn('recipients', response.context['form'].errors)
        # Being a primary client of this project does not grant internal access.
        ProjectMembership.objects.create(project=self.project, user=self.unassigned, role='client')
        self.client.force_login(self.unassigned)
        self.assertEqual(self.client.get(self.compose_url(True)).status_code, 404)
        self.client.force_login(self.sender)
        response = self.client.post(self.compose_url(True), self.data(recipients=[self.unassigned.pk]))
        self.assertIn('recipients', response.context['form'].errors)

    def test_other_company_project_and_invalid_context_rejected(self):
        base = reverse('projects:staff_message_compose')
        self.assertEqual(self.client.get(base + f'?project={self.other_project.pk}').status_code, 404)
        self.assertEqual(self.client.get(base + f'?project={self.project.pk}&organization={self.other_org.pk}').status_code, 403)
        for query in ('?organization=bad', '?project=bad', '?project=999999999999999999999999999', '?organization=-1'):
            self.assertEqual(self.client.get(base + query).status_code, 404)

    def test_email_contains_full_message_and_authenticated_reply_link(self):
        body = 'Please confirm the countertop finish.\nUse the agreed specification.'
        thread = self.create_thread(project=True, body=body, recipients=[self.receiver.pk, self.admin.pk])
        self.assertEqual(process_email_queue(), (2, 0, 0))
        self.assertEqual(len(mail.outbox), 2)
        for message in mail.outbox:
            self.assertEqual(len(message.to), 1)
            self.assertIn(body, message.body)
            self.assertIn(self.detail_url(thread), message.body)
            self.assertIn(self.project.name, message.body)
            self.assertEqual(message.content_subtype, 'plain')
        self.assertEqual(process_email_queue(), (0, 0, 0))
        self.assertEqual(len(mail.outbox), 2)
        self.client.logout()
        self.assertEqual(self.client.get(self.detail_url(thread)).status_code, 302)

    def test_duplicate_submission_and_reply_do_not_duplicate_messages_or_alerts(self):
        data = self.data()
        self.client.post(self.compose_url(), data)
        self.client.post(self.compose_url(), data)
        thread = StaffThread.objects.get()
        self.assertEqual(StaffMessage.objects.count(), 1)
        self.client.force_login(self.receiver)
        data = {'body': 'Confirmed.', 'submission_id': str(uuid.uuid4())}
        self.client.post(self.detail_url(thread), data)
        self.client.post(self.detail_url(thread), data)
        self.assertEqual(thread.messages.count(), 2)
        self.assertEqual(StaffMessageDelivery.objects.count(), 2)
        self.assertEqual(InAppNotification.objects.count(), 2)

    def test_reply_notifies_others_but_not_author(self):
        thread = self.create_thread(recipients=[self.receiver.pk, self.admin.pk])
        self.client.force_login(self.receiver)
        self.client.post(self.detail_url(thread), {'body': 'Answer', 'submission_id': str(uuid.uuid4())})
        reply = thread.messages.latest('pk')
        self.assertEqual(set(reply.deliveries.values_list('recipient_id', flat=True)), {self.sender.pk, self.admin.pk})

    def test_reading_thread_marks_alerts_read_and_clears_inbox_badge(self):
        thread = self.create_thread()
        self.client.force_login(self.receiver)
        response = self.client.get(reverse('projects:staff_message_inbox') + '?unread=1')
        self.assertContains(response, thread.subject)
        self.assertContains(response, '1 unread')
        self.client.get(self.detail_url(thread))
        self.assertFalse(visible_notifications(self.receiver).filter(read_at__isnull=True).exists())
        self.assertNotContains(self.client.get(reverse('projects:staff_message_inbox') + '?unread=1'), thread.subject)

    def test_muting_email_keeps_in_app_alert_and_skips_already_queued_email(self):
        thread = self.create_thread()
        self.client.force_login(self.receiver)
        self.client.post(reverse('projects:staff_message_preferences', args=[thread.pk]), {})
        self.assertEqual(process_email_queue(), (0, 1, 0))
        self.assertEqual(InAppNotification.objects.count(), 1)
        self.client.force_login(self.sender)
        self.client.post(self.detail_url(thread), {'body': 'Follow-up', 'submission_id': str(uuid.uuid4())})
        self.assertEqual(thread.messages.latest('pk').deliveries.get().email_status, 'disabled')
        self.assertEqual(InAppNotification.objects.count(), 2)

    def test_revoking_project_access_hides_thread_alert_and_queued_email(self):
        thread = self.create_thread(project=True)
        notification = InAppNotification.objects.get()
        self.receiver.organization_memberships.get().project_access.update(is_active=False)
        self.client.force_login(self.receiver)
        self.assertEqual(self.client.get(self.detail_url(thread)).status_code, 404)
        self.assertNotContains(self.client.get(reverse('projects:notification_list')), thread.subject)
        self.assertEqual(self.client.post(reverse('projects:notification_mark_read', args=[notification.pk])).status_code, 404)
        self.assertEqual(process_email_queue(), (0, 1, 0))
        self.assertEqual(len(mail.outbox), 0)

    def test_demoting_or_deactivating_staff_revokes_general_conversation_access(self):
        thread = self.create_thread()
        membership = self.receiver.organization_memberships.get()
        membership.role = 'client'
        membership.save()
        self.assertFalse(staff_threads(self.receiver).exists())
        self.assertFalse(visible_notifications(self.receiver).exists())
        self.assertEqual(process_email_queue(), (0, 1, 0))
        self.assertTrue(StaffParticipant.objects.filter(thread=thread, user=self.receiver).exists())

    def test_failed_email_preserves_message_and_alert_without_automatic_resend(self):
        self.create_thread()
        with patch('projects.staff_messaging.send_notification_email', return_value=None):
            self.assertEqual(process_email_queue(), (0, 0, 1))
        self.assertEqual(StaffMessageDelivery.objects.get().email_status, 'failed')
        self.assertEqual(StaffMessage.objects.count(), 1)
        self.assertEqual(InAppNotification.objects.count(), 1)
        self.assertEqual(process_email_queue(), (0, 0, 0))

    def test_abandoned_send_is_reported_without_resending_ambiguous_email(self):
        self.create_thread()
        StaffMessageDelivery.objects.update(email_status='sending', attempted_at=timezone.now() - timedelta(minutes=16))
        with patch('projects.staff_messaging.record_operational_event') as report:
            self.assertEqual(process_email_queue(), (0, 0, 1))
            report.assert_called_once()
            self.assertEqual(process_email_queue(), (0, 0, 0))
            report.assert_called_once()
        self.assertEqual(len(mail.outbox), 0)

    def test_client_preview_cannot_open_or_send_staff_messages(self):
        thread = self.create_thread(project=True)
        self.client.post(reverse('projects:client_preview_start'), {'project': self.project.pk})
        self.assertEqual(self.client.get(self.detail_url(thread)).status_code, 403)
        self.assertEqual(self.client.post(self.compose_url(), self.data()).status_code, 403)
        self.assertNotContains(self.client.get(reverse('projects:detail', args=[self.project.pk])), 'Staff messages')

    def test_markup_is_escaped_and_private_audit_has_no_message_content(self):
        thread = self.create_thread(subject='<script>secret</script>', body='<img src=x onerror=alert(1)>')
        response = self.client.get(self.detail_url(thread))
        self.assertContains(response, '&lt;img')
        self.assertNotContains(response, '<img src=x')
        event = ActivityEvent.objects.get(event_type='staff_message_sent')
        self.assertNotIn('secret', event.summary)
        self.assertNotIn('body', event.metadata)

    @override_settings(TURNSTILE_FORMS_ENABLED=True, TURNSTILE_SITE_KEY='test', TURNSTILE_SECRET_KEY='test')
    def test_new_message_form_remains_captcha_protected(self):
        response = self.client.post(self.compose_url(), self.data())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(StaffThread.objects.exists())

    def test_worker_preserves_morning_deadline_window_and_reports_mail_failure(self):
        path = 'projects.management.commands.process_notifications'
        with patch(path + '.timezone.now', return_value=datetime(2026, 11, 20, 13, tzinfo=dt_timezone.utc)), patch(path + '.call_command') as deadlines:
            call_command('process_notifications', stdout=io.StringIO())
            deadlines.assert_not_called()
        with patch(path + '.timezone.now', return_value=datetime(2026, 11, 20, 14, tzinfo=dt_timezone.utc)), patch(path + '.call_command') as deadlines:
            call_command('process_notifications', stdout=io.StringIO())
            deadlines.assert_called_once()
        self.create_thread()
        with patch('projects.staff_messaging.send_notification_email', return_value=None):
            with self.assertRaises(CommandError):
                call_command('process_notifications', stdout=io.StringIO())
