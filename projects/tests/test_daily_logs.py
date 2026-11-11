import shutil
import tempfile
from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from projects.models import (
    ActivityEvent,
    DailyLog,
    DailyLogPhoto,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
)
from projects.storage import private_document_storage
from projects.tests import grant_internal_access


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class DailyLogTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_media_directory = Path(tempfile.mkdtemp(prefix='daily-logs-'))
        cls.private_media_override = override_settings(
            PRIVATE_MEDIA_ROOT=cls.private_media_directory
        )
        cls.private_media_override.enable()
        for attribute in ('base_location', 'location', 'base_url'):
            private_document_storage.__dict__.pop(attribute, None)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.private_media_override.disable()
        for attribute in ('base_location', 'location', 'base_url'):
            private_document_storage.__dict__.pop(attribute, None)
        shutil.rmtree(cls.private_media_directory, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(
            name='Heritage Builders', slug='heritage-builders'
        )
        cls.project = Project.objects.create(
            organization=cls.organization,
            name='Oak Street',
            status=Project.Status.ACTIVE,
        )
        cls.admin_user = get_user_model().objects.create_user(
            'admin@example.com', 'password'
        )
        cls.staff_user = get_user_model().objects.create_user(
            'staff@example.com', 'password'
        )
        cls.client_user = get_user_model().objects.create_user(
            'client@example.com', 'password'
        )
        for user, role in (
            (cls.admin_user, OrganizationMembership.Role.ADMIN),
            (cls.staff_user, OrganizationMembership.Role.STAFF),
            (cls.client_user, OrganizationMembership.Role.CLIENT),
        ):
            OrganizationMembership.objects.create(
                organization=cls.organization, user=user, role=role
            )
        ProjectMembership.objects.create(
            project=cls.project,
            user=cls.client_user,
            role=OrganizationMembership.Role.CLIENT,
        )
        grant_internal_access(
            cls.staff_user,
            cls.project,
            can_manage=False,
            can_invite_clients=False,
        )

    def create_log(self, *, client_visible=False, title='Framing progress'):
        return DailyLog.objects.create(
            project=self.project,
            log_date=date(2026, 11, 11),
            title=title,
            summary='Exterior walls are framed and ready for roof trusses.',
            client_visible=client_visible,
            created_by=self.admin_user,
            published_by=self.admin_user if client_visible else None,
            published_at=timezone.now() if client_visible else None,
        )

    def form_data(self, **overrides):
        data = {
            'log_date': '2026-11-11',
            'title': 'Framing progress',
            'summary': 'Exterior walls are framed and ready for roof trusses.',
        }
        data.update(overrides)
        return data

    def photo_upload(self, name='framing.jpg'):
        return SimpleUploadedFile(name, b'photo-bytes', content_type='image/jpeg')

    def test_admin_can_save_internal_update_without_emailing_client(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse('projects:daily_log_create', args=(self.project.pk,)),
            self.form_data(),
        )

        daily_log = DailyLog.objects.get()
        self.assertRedirects(
            response,
            reverse(
                'projects:daily_log_detail',
                args=(self.project.pk, daily_log.pk),
            ),
        )
        self.assertFalse(daily_log.client_visible)
        self.assertIsNone(daily_log.published_at)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.DAILY_LOG_CREATED,
                project=self.project,
            ).exists()
        )

    def test_client_sees_only_published_updates(self):
        internal_log = self.create_log(title='Internal coordination')
        published_log = self.create_log(
            client_visible=True, title='Weekly homeowner update'
        )
        self.client.force_login(self.client_user)

        response = self.client.get(
            reverse('projects:daily_log_list', args=(self.project.pk,))
        )

        self.assertContains(response, published_log.title)
        self.assertNotContains(response, internal_log.title)
        internal_response = self.client.get(
            reverse(
                'projects:daily_log_detail',
                args=(self.project.pk, internal_log.pk),
            )
        )
        self.assertEqual(internal_response.status_code, 404)
        self.assertNotContains(response, 'New update')

    def test_publishing_update_notifies_client_and_records_actor(self):
        daily_log = self.create_log()
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse(
                'projects:daily_log_edit', args=(self.project.pk, daily_log.pk)
            ),
            self.form_data(client_visible='on'),
        )
        daily_log.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertTrue(daily_log.client_visible)
        self.assertEqual(daily_log.published_by, self.admin_user)
        self.assertIsNotNone(daily_log.published_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.client_user.email])
        self.assertIn('Project update', mail.outbox[0].subject)
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.DAILY_LOG_PUBLISHED,
                project=self.project,
            ).exists()
        )

    def test_read_only_staff_cannot_create_or_edit_updates(self):
        daily_log = self.create_log()
        self.client.force_login(self.staff_user)

        create_response = self.client.post(
            reverse('projects:daily_log_create', args=(self.project.pk,)),
            self.form_data(),
        )
        edit_response = self.client.post(
            reverse(
                'projects:daily_log_edit', args=(self.project.pk, daily_log.pk)
            ),
            self.form_data(title='Unauthorized change'),
        )

        self.assertEqual(create_response.status_code, 403)
        self.assertEqual(edit_response.status_code, 403)
        daily_log.refresh_from_db()
        self.assertEqual(daily_log.title, 'Framing progress')

    def test_photo_upload_is_private_and_available_to_published_client(self):
        daily_log = self.create_log(client_visible=True)
        self.client.force_login(self.admin_user)
        upload_response = self.client.post(
            reverse(
                'projects:daily_log_photo_create',
                args=(self.project.pk, daily_log.pk),
            ),
            {
                'image': self.photo_upload(),
                'caption': 'Roof trusses staged on site',
                'sort_order': '1',
            },
        )
        photo = DailyLogPhoto.objects.get()
        self.assertEqual(upload_response.status_code, 302)
        with self.assertRaises(ValueError):
            _ = photo.image.url

        self.client.force_login(self.client_user)
        response = self.client.get(
            reverse(
                'projects:daily_log_photo',
                args=(self.project.pk, daily_log.pk, photo.pk),
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_client_cannot_upload_or_remove_progress_photos(self):
        daily_log = self.create_log(client_visible=True)
        photo = DailyLogPhoto.objects.create(
            daily_log=daily_log,
            image=self.photo_upload(),
            original_filename='framing.jpg',
            uploaded_by=self.admin_user,
        )
        self.client.force_login(self.client_user)

        upload_response = self.client.post(
            reverse(
                'projects:daily_log_photo_create',
                args=(self.project.pk, daily_log.pk),
            ),
            {'image': self.photo_upload('other.jpg'), 'sort_order': '0'},
        )
        delete_response = self.client.post(
            reverse(
                'projects:daily_log_photo_delete',
                args=(self.project.pk, daily_log.pk, photo.pk),
            )
        )

        self.assertEqual(upload_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)
        self.assertTrue(DailyLogPhoto.objects.filter(pk=photo.pk).exists())
