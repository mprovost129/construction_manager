import io
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from projects.deadlines import scheduled_today, send_deadline
from projects.models import (
    DeadlineReminder,
    FinishSelection,
    InAppNotification,
    NotificationPreference,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
    PunchItem,
)
from projects.tests import grant_internal_access


class DeadlinePunchTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        override = override_settings(PRIVATE_MEDIA_ROOT=Path(self.media.name))
        override.enable()
        self.addCleanup(override.disable)
        self.org = Organization.objects.create(name='Test Builder', slug='test-builder')
        self.project = Project.objects.create(organization=self.org, name='Test House')
        self.other = Project.objects.create(organization=self.org, name='Other House')
        self.admin = self.user('admin', 'admin')
        self.staff = self.user('staff', 'staff')
        self.outsider = self.user('outsider', 'staff')
        self.customer = self.user('customer', 'client')
        self.accountant = self.user('accountant', 'accountant')
        grant_internal_access(self.staff, self.project, can_manage=False)
        grant_internal_access(self.outsider, self.other)
        grant_internal_access(self.accountant, self.project)
        ProjectMembership.objects.create(project=self.project, user=self.customer, role='client')
        self.item = PunchItem.objects.create(project=self.project, title='Adjust cabinet door',
            description='Align the door.', internal_notes='PRIVATE COMPANY NOTE',
            assigned_to=self.staff, created_by=self.admin, due_date=timezone.localdate())
        self.selection = FinishSelection.objects.create(project=self.project, number=1,
            title='Tile choice', status='open', opened_by=self.admin, opened_at=timezone.now(),
            created_by=self.admin, due_date=timezone.localdate())
        self.client.force_login(self.admin)

    def user(self, name, role):
        user = get_user_model().objects.create_user(f'{name}@example.com', 'test-only')
        OrganizationMembership.objects.create(organization=self.org, user=user, role=role)
        return user

    def url(self, name='punch_detail', **kwargs):
        return reverse(f'projects:{name}', kwargs={'pk': self.project.pk,
            **({'item_pk': self.item.pk} if name in ('punch_detail', 'punch_edit', 'punch_photo') else {}), **kwargs})

    def image(self):
        data = io.BytesIO()
        Image.new('RGB', (10, 10)).save(data, format='PNG')
        return SimpleUploadedFile('test.png', data.getvalue(), content_type='image/png')

    def test_client_cannot_list_open_or_edit_internal_items(self):
        self.client.force_login(self.customer)
        self.assertNotContains(self.client.get(self.url('punch_list')), self.item.title)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(self.client.get(self.url('punch_edit')).status_code, 403)
        self.assertEqual(self.client.post(self.url('punch_create'), {'title': 'Injected'}).status_code, 403)

    def test_shared_client_view_excludes_private_fields_and_all_writes(self):
        self.item.client_visible = True
        self.item.save()
        self.client.force_login(self.customer)
        response = self.client.get(self.url())
        self.assertContains(response, self.item.description)
        for hidden in ('PRIVATE COMPANY NOTE', self.staff.email, 'Update progress', 'Edit details'):
            self.assertNotContains(response, hidden)
        self.assertEqual(self.client.post(self.url(), {'action': 'ready'}).status_code, 403)

    def test_cross_project_and_accountant_access_denied(self):
        for user in (self.outsider, self.accountant):
            self.client.force_login(user)
            self.assertIn(self.client.get(self.url()).status_code, (403, 404))
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('projects:punch_detail', args=[self.other.pk, self.item.pk])).status_code, 404)

    def test_only_assigned_staff_can_progress_and_manager_must_verify(self):
        grant_internal_access(self.outsider, self.project, can_manage=False)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(self.url(), {'action': 'ready'}).status_code, 403)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(self.url(), {'action': 'ready'}).status_code, 302)
        self.assertEqual(self.client.post(self.url(), {'action': 'verified'}).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url(), {'action': 'verified'}).status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.verified_by, self.admin)
        self.client.post(self.url(), {'action': 'open'})
        self.item.refresh_from_db()
        self.assertIsNone(self.item.verified_at)

    def test_cannot_verify_open_item(self):
        self.assertEqual(self.client.post(self.url(), {'action': 'verified'}).status_code, 403)

    def test_assignment_rejects_other_project_and_client_and_accountant(self):
        for user in (self.outsider, self.customer, self.accountant):
            response = self.client.post(self.url('punch_create'), {
                'title': 'Door repair', 'assigned_to': user.pk, 'priority': 'normal'})
            self.assertEqual(response.status_code, 200)
            self.assertIn('assigned_to', response.context['form'].errors)
        response = self.client.post(self.url('punch_create'), {
            'title': 'Door repair', 'assigned_to': self.staff.pk, 'priority': 'normal'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(PunchItem.objects.get(title='Door repair').client_visible)

    def test_photo_validation_and_private_download_revocation(self):
        response = self.client.post(self.url(), {'action': 'photo', 'image': self.image(), 'caption': 'Door view'})
        self.assertEqual(response.status_code, 302)
        photo = self.item.photos.get()
        url = self.url('punch_photo', photo_pk=photo.pk)
        response = self.client.get(url)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['Content-Type'], 'image/png')
        response.close()
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.item.client_visible = True
        self.item.save()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response.close()
        self.item.client_visible = False
        self.item.save()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_fake_image_rejected(self):
        response = self.client.post(self.url(), {'action': 'photo', 'image':
            SimpleUploadedFile('attack.png', b'<script>alert(1)</script>', content_type='image/png')})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.item.photos.exists())
        self.assertIn('image', response.context['photo_form'].errors)

    def test_client_preview_uses_client_visibility_and_cannot_post(self):
        self.client.post(reverse('projects:client_preview_start'), {'project': self.project.pk})
        self.assertNotContains(self.client.get(self.url('punch_list')), self.item.title)
        self.item.client_visible = True
        self.item.save()
        self.assertContains(self.client.get(self.url()), self.item.title)
        self.assertNotContains(self.client.get(self.url()), self.item.internal_notes)
        self.assertEqual(self.client.post(self.url(), {'action': 'ready'}).status_code, 403)

    def test_selection_reminder_deduplicates_manual_and_cron(self):
        self.assertEqual(send_deadline(self.selection), 1)
        call_command('send_deadline_reminders', stdout=io.StringIO())
        self.client.post(reverse('projects:selection_remind', args=[self.project.pk, self.selection.pk]))
        self.assertEqual(DeadlineReminder.objects.filter(selection=self.selection).count(), 1)
        self.assertEqual(len([m for m in mail.outbox if self.customer.email in m.to]), 1)
        self.assertEqual(InAppNotification.objects.filter(user=self.customer).count(), 1)

    def test_reminder_channels_and_mail_failure_do_not_duplicate_in_app(self):
        with patch('projects.deadlines.send_notification_email', return_value=None):
            self.assertEqual(send_deadline(self.selection), 1)
            self.assertEqual(send_deadline(self.selection), 0)
        self.assertEqual(DeadlineReminder.objects.get().email_status, 'failed')
        self.assertEqual(InAppNotification.objects.filter(user=self.customer).count(), 1)

    def test_disabled_email_still_delivers_one_in_app_alert(self):
        NotificationPreference.objects.create(user=self.customer, project=self.project,
            event_type='selections', email_enabled=False, in_app_enabled=True)
        send_deadline(self.selection)
        send_deadline(self.selection)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(InAppNotification.objects.filter(user=self.customer).count(), 1)

    def test_all_alerts_disabled_and_inactive_recipient_are_skipped(self):
        preference = NotificationPreference.objects.create(user=self.customer, project=self.project,
            event_type='selections', email_enabled=False, in_app_enabled=False)
        self.assertEqual(send_deadline(self.selection), 0)
        preference.delete()
        self.customer.is_active = False
        self.customer.save()
        self.assertEqual(send_deadline(self.selection), 0)
        self.assertEqual(DeadlineReminder.objects.count(), 0)

    def test_deadline_schedule_and_dry_run_do_not_send_or_write(self):
        today = timezone.localdate()
        expected = {-15, -8, -1, 0, 1, 3}
        self.assertEqual({d for d in range(-16, 5) if scheduled_today(today + timedelta(days=d), today)}, expected)
        call_command('send_deadline_reminders', dry_run=True, stdout=io.StringIO())
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(DeadlineReminder.objects.count(), 0)
        self.assertEqual(InAppNotification.objects.count(), 0)

    def test_punch_deadlines_only_notify_active_assignee_not_client(self):
        self.item.client_visible = True
        self.item.save()
        self.assertEqual(send_deadline(self.item), 1)
        self.assertEqual(mail.outbox[0].to, [self.staff.email])
        self.assertNotIn(self.item.internal_notes, mail.outbox[0].body)

    def test_closed_and_paused_work_receives_no_reminders(self):
        self.item.status = 'ready'
        self.item.save()
        self.assertEqual(send_deadline(self.item), 0)
        self.project.status = 'on_hold'
        self.project.save()
        self.selection.project = self.project
        self.assertEqual(send_deadline(self.selection), 0)
        self.project.status = 'active'
        self.project.save()
        self.selection.status = 'draft'
        self.assertEqual(send_deadline(self.selection), 0)

    def test_revoked_assignment_receives_no_reminder(self):
        self.staff.organization_memberships.get().project_access.update(is_active=False)
        self.assertEqual(send_deadline(self.item), 0)

    def test_notification_preferences_include_punch_lists(self):
        response = self.client.get(reverse('projects:notification_settings', args=[self.project.pk]))
        self.assertContains(response, 'Punch-list deadlines')

    def test_deadline_edit_and_pause_require_project_management_access(self):
        url = reverse('projects:selection_deadline', args=[self.project.pk, self.selection.pk])
        for user in (self.customer, self.staff, self.outsider):
            self.client.force_login(user)
            self.assertIn(self.client.post(url, {'due_date': '2026-12-01'}).status_code, (403, 404))
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(url, {'due_date': '2026-12-01'}).status_code, 302)
        self.selection.refresh_from_db()
        self.assertEqual(self.selection.due_date.isoformat(), '2026-12-01')
        self.assertFalse(self.selection.deadline_reminders_enabled)
        self.assertEqual(self.selection.status, 'open')
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_paused_deadline_ignored_by_scheduler(self):
        self.selection.deadline_reminders_enabled = False
        self.selection.save()
        self.item.deadline_reminders_enabled = False
        self.item.save()
        call_command('send_deadline_reminders', stdout=io.StringIO())
        self.assertFalse(DeadlineReminder.objects.exists())

    def test_mail_failure_marks_scheduled_run_failed_without_losing_alerts(self):
        with patch('projects.deadlines.send_notification_email', return_value=None):
            with self.assertRaises(CommandError):
                call_command('send_deadline_reminders', stdout=io.StringIO())
        self.assertEqual(InAppNotification.objects.count(), 2)
        self.assertEqual(DeadlineReminder.objects.filter(email_status='failed').count(), 2)

    def test_admin_of_other_company_is_not_assignable_without_project_access(self):
        other_org = Organization.objects.create(name='Other company', slug='other-company')
        OrganizationMembership.objects.create(organization=other_org, user=self.outsider, role='admin')
        response = self.client.post(self.url('punch_create'), {
            'title': 'Door repair', 'assigned_to': self.outsider.pk, 'priority': 'normal'})
        self.assertIn('assigned_to', response.context['form'].errors)

    def test_active_staff_without_assignment_cannot_manage_internal_item(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(self.client.post(self.url(), {'action': 'ready'}).status_code, 404)
