from datetime import date
from io import BytesIO
from unittest.mock import patch

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from projects.access import projects_for_user
from projects.deadlines import due_items
from projects.models import (
    ChoiceImage,
    DailyLog,
    DailyLogPhoto,
    FinishSelection,
    InAppNotification,
    Organization,
    Project,
    ProjectInvitation,
    SelectionOption,
    SelectionPackage,
)
from projects.services import deliver_project_notification
from projects.staff_access import visible_notifications
from projects.storage import private_document_storage
from projects.tests.test_daily_logs import DailyLogTests


def image_upload(name="progress.jpg"):
    output = BytesIO()
    with Image.new("RGB", (1200, 600), "green") as image:
        image.save(output, "JPEG")
    return SimpleUploadedFile(name, output.getvalue(), content_type="image/jpeg")


class ProjectManagementTests(DailyLogTests):
    def test_multiple_photos_saved_before_publication_and_private_thumbnails(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("projects:daily_log_create", args=[self.project.pk]),
            self.form_data(images=[image_upload(), image_upload("other.jpg")]),
        )
        self.assertEqual(response.status_code, 302)
        log = DailyLog.objects.get()
        self.assertEqual(log.photos.count(), 2)
        self.assertEqual(len(mail.outbox), 0)
        photo = log.photos.first()
        url = reverse(
            "projects:daily_log_photo", args=[self.project.pk, log.pk, photo.pk]
        )
        response = self.client.get(url, {"thumb": "1"})
        self.assertEqual(response["Cache-Control"], "private, no-store")
        with Image.open(BytesIO(response.content)) as image:
            self.assertEqual(image.size, (480, 240))
        self.client.force_login(self.client_user)
        self.assertEqual(self.client.get(url, {"thumb": "1"}).status_code, 404)
        self.client.force_login(self.admin_user)
        with patch("projects.views.send_daily_log_published_notification") as notify:
            notify.return_value = 0
            notify.side_effect = lambda request, daily_log: (
                self.assertEqual(daily_log.photos.count(), 3) or 0
            )
            self.client.post(
                reverse("projects:daily_log_edit", args=[self.project.pk, log.pk]),
                self.form_data(client_visible="on", images=[image_upload("new.jpg")]),
            )
            notify.assert_called_once()
        self.client.force_login(self.client_user)
        self.assertEqual(self.client.get(url, {"thumb": "1"}).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(url, {"thumb": "1"}).status_code, 302)

    def test_every_upload_validated_before_saving_update(self):
        self.client.force_login(self.admin_user)
        url = reverse("projects:daily_log_create", args=[self.project.pk])
        response = self.client.post(
            url,
            self.form_data(
                client_visible="on",
                images=[image_upload(), SimpleUploadedFile("bad.jpg", b"not an image")],
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors["images"])
        self.assertFalse(DailyLog.objects.exists())
        self.assertFalse(DailyLogPhoto.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_failed_photo_storage_rolls_back_update_and_cleans_completed_files(self):
        self.client.force_login(self.admin_user)
        original_save = private_document_storage._save
        names = []

        def fail_second(name, content):
            if names:
                raise OSError("Synthetic storage interruption")
            stored = original_save(name, content)
            names.append(stored)
            return stored

        with patch.object(private_document_storage, "_save", side_effect=fail_second):
            response = self.client.post(
                reverse("projects:daily_log_create", args=[self.project.pk]),
                self.form_data(
                    client_visible="on",
                    images=[image_upload(), image_upload("second.jpg")],
                ),
            )
        self.assertContains(response, "could not be saved")
        self.assertFalse(DailyLog.objects.exists())
        self.assertFalse(DailyLogPhoto.objects.exists())
        self.assertFalse(private_document_storage.exists(names[0]))
        self.assertEqual(len(mail.outbox), 0)

    def test_delete_hides_all_project_access_and_restore_preserves_records(self):
        log = self.create_log(client_visible=True)
        photo = DailyLogPhoto.objects.create(
            daily_log=log,
            image=image_upload(),
            original_filename="progress.jpg",
            uploaded_by=self.admin_user,
        )
        invite = ProjectInvitation.objects.create(
            project=self.project, email="new@example.com", invited_by=self.admin_user
        )
        notification = InAppNotification.objects.create(
            user=self.client_user,
            project=self.project,
            event_type="daily_logs",
            title="Progress",
            message="Photo",
            url_path="/",
        )
        FinishSelection.objects.create(
            project=self.project,
            number=1,
            title="Due today",
            status="open",
            created_by=self.admin_user,
            opened_by=self.admin_user,
            opened_at=timezone.now(),
            due_date=timezone.localdate(),
        )
        self.client.force_login(self.admin_user)
        delete_url = reverse("projects:delete", args=[self.project.pk])
        self.assertEqual(self.client.get(delete_url).status_code, 200)
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())
        self.client.post(delete_url, {"name": "wrong"})
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())
        self.assertEqual(
            self.client.post(delete_url, {"name": self.project.name}).status_code, 302
        )
        self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())
        self.assertTrue(Project.all_objects.filter(pk=self.project.pk).exists())
        self.assertEqual(list(due_items(timezone.localdate())), [])
        self.assertFalse(
            visible_notifications(self.client_user).filter(pk=notification.pk).exists()
        )
        self.assertEqual(
            deliver_project_notification(
                request=None,
                project=self.project,
                event_type="messages",
                title="Old update",
                message="Old",
                url_path="/",
                recipient_list=[self.client_user.email],
            ),
            0,
        )
        invite.refresh_from_db()
        self.assertFalse(invite.is_valid)
        for user in (self.admin_user, self.client_user, self.staff_user):
            self.client.force_login(user)
            self.assertFalse(
                projects_for_user(user).filter(pk=self.project.pk).exists()
            )
            for url in (
                reverse("projects:detail", args=[self.project.pk]),
                reverse(
                    "projects:daily_log_photo", args=[self.project.pk, log.pk, photo.pk]
                )
                + "?thumb=1",
            ):
                self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.admin_user)
        self.assertContains(
            self.client.get(reverse("projects:deleted_projects")), self.project.name
        )
        self.assertEqual(
            self.client.post(
                reverse("projects:restore", args=[self.project.pk]),
                {"name": self.project.name},
            ).status_code,
            302,
        )
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())
        self.assertTrue(private_document_storage.exists(photo.image.name))
        self.client.force_login(self.client_user)
        self.assertEqual(
            self.client.get(
                reverse("projects:daily_log_detail", args=[self.project.pk, log.pk])
            ).status_code,
            200,
        )
        invite.refresh_from_db()
        self.assertFalse(invite.is_valid)

    def test_project_delete_copy_and_restore_are_permission_scoped(self):
        other = Project.objects.create(
            organization=Organization.objects.create(name="Other", slug="other"),
            name="Other project",
        )
        for user in (self.client_user, self.staff_user):
            self.client.force_login(user)
            for action in ("delete", "copy", "restore"):
                response = self.client.post(
                    reverse("projects:" + action, args=[self.project.pk]),
                    {"name": self.project.name},
                )
                self.assertIn(response.status_code, (403, 404))
            self.assertEqual(
                self.client.get(reverse("projects:deleted_projects")).status_code, 403
            )
        self.client.force_login(self.admin_user)
        for action in ("delete", "copy", "restore"):
            self.assertEqual(
                self.client.post(
                    reverse("projects:" + action, args=[other.pk]), {"name": other.name}
                ).status_code,
                404,
            )
        self.assertEqual(Project.objects.count(), 2)

    def test_copy_checkboxes_copy_only_selected_setup_and_reset_decisions(self):
        self.project.description = "Staff notes"
        self.project.start_date = date(2026, 11, 1)
        self.project.contract_amount = 500000
        self.project.save()
        package = SelectionPackage.objects.create(
            project=self.project, title="Cabinets", created_by=self.admin_user
        )
        row = FinishSelection.objects.create(
            project=self.project,
            number=1,
            title="Kitchen cabinets",
            package=package,
            allowance_amount=20000,
            due_date=timezone.localdate(),
            created_by=self.admin_user,
            status="open",
            opened_by=self.admin_user,
            opened_at=timezone.now(),
        )
        option = SelectionOption.objects.create(
            selection=row, name="Oak", price=21000, image=image_upload()
        )
        ChoiceImage.objects.create(
            option=option, image=image_upload("side.jpg"), caption="Side view"
        )
        row.status = "selected"
        row.chosen_option = option
        row.selected_by = self.client_user
        row.selected_at = timezone.now()
        row.save()
        FinishSelection.objects.create(
            project=self.project,
            number=2,
            title="Do not copy",
            created_by=self.admin_user,
        )
        self.client.force_login(self.admin_user)
        url = reverse("projects:copy", args=[self.project.pk])
        self.assertContains(self.client.get(url), "Select all selection rows")
        response = self.client.post(
            url,
            {
                "name": "Fresh project",
                "notes": "on",
                "selections": [row.pk],
                "choices": "on",
                "photos": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        new = Project.objects.get(name="Fresh project")
        self.assertEqual(new.description, "Staff notes")
        self.assertIsNone(new.start_date)
        self.assertIsNone(new.contract_amount)
        self.assertFalse(new.project_memberships.exists())
        self.assertEqual(new.finish_selections.count(), 1)
        copied = new.finish_selections.get()
        self.assertEqual(copied.status, "draft")
        self.assertIsNone(copied.selected_at)
        self.assertIsNone(copied.chosen_option_id)
        self.assertIsNone(copied.due_date)
        self.assertFalse(copied.import_source)
        copied_option = copied.options.get()
        self.assertEqual(copied_option.price, option.price)
        self.assertNotEqual(copied_option.image.name, option.image.name)
        self.assertTrue(private_document_storage.exists(copied_option.image.name))
        self.assertEqual(copied_option.gallery_images.get().caption, "Side view")
        self.assertEqual(len(mail.outbox), 0)
        self.client.post(
            url, {"name": "Rows only", "selections": [row.pk], "dates": "on"}
        )
        bare = Project.objects.get(name="Rows only")
        self.assertEqual(bare.start_date, self.project.start_date)
        self.assertEqual(bare.description, "")
        self.assertFalse(bare.finish_selections.get().options.exists())
        self.client.post(
            url,
            {"name": "Choices without photos", "selections": [row.pk], "choices": "on"},
        )
        no_photos = (
            Project.objects.get(name="Choices without photos")
            .finish_selections.get()
            .options.get()
        )
        self.assertFalse(no_photos.image)
        self.assertFalse(no_photos.gallery_images.exists())

    def test_copy_rejects_selection_from_another_project(self):
        other = Project.objects.create(organization=self.organization, name="Other")
        row = FinishSelection.objects.create(
            project=other, number=1, title="Other selection", created_by=self.admin_user
        )
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("projects:copy", args=[self.project.pk]),
            {"name": "Forged", "selections": [row.pk]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Project.objects.filter(name="Forged").exists())
