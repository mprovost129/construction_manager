from django.core import mail
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from projects.deadlines import send_deadline
from projects.models import FinishSelection, InAppNotification
from projects.services import send_selection_review_notification
from projects.tests import test_selections


class SelectionPublishMessageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_selections.FinishSelectionTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.selection, self.option = (
            test_selections.FinishSelectionTests.create_selection(self)
        )
        self.url = reverse(
            "projects:selection_publish", args=[self.project.pk, self.selection.pk]
        )
        self.client.force_login(self.staff_user)

    def test_publish_preview_and_note_in_first_notification_only(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Message to client")
        self.assertContains(response, self.client_user.email)
        self.assertFalse(mail.outbox)
        note = "Please review the island finish.\nLet me know by Friday."
        description = self.selection.description
        response = self.client.post(self.url, {"message_to_client": note})
        self.assertEqual(response.status_code, 302)
        self.selection.refresh_from_db()
        self.assertEqual(self.selection.description, description)
        self.assertEqual(self.selection.status, FinishSelection.Status.OPEN)
        self.assertIn(note, mail.outbox[0].body)
        self.assertIn(
            reverse(
                "projects:selection_detail", args=[self.project.pk, self.selection.pk]
            ),
            mail.outbox[0].body,
        )
        alerts = InAppNotification.objects.filter(project=self.project)
        self.assertTrue(alerts.exists())
        self.assertTrue(all(note in item.message for item in alerts))
        count = len(mail.outbox)
        self.assertEqual(
            self.client.post(self.url, {"message_to_client": note}).status_code, 403
        )
        self.assertEqual(len(mail.outbox), count)
        # Both manual and scheduled reminders use this shared deadline sender.
        mail.outbox.clear()
        alerts.delete()
        self.assertGreater(send_deadline(self.selection, today=timezone.localdate()), 0)
        self.assertTrue(mail.outbox)
        self.assertTrue(all(note not in item.body for item in mail.outbox))
        self.assertFalse(
            InAppNotification.objects.filter(message__contains=note).exists()
        )
        # Reopen notifications cannot recover/replay the original publishing note.
        mail.outbox.clear()
        request = RequestFactory().get("/")
        request.user = self.staff_user
        send_selection_review_notification(request, self.selection)
        self.assertNotIn(note, mail.outbox[0].body)

    def test_blank_note_keeps_existing_publish_flow(self):
        response = self.client.post(self.url, {"message_to_client": "   "})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("Message to client:", mail.outbox[0].body)

    def test_invalid_note_preserved_without_publishing(self):
        note = "a" * 5001
        response = self.client.post(self.url, {"message_to_client": note})
        self.assertContains(response, "at most 5000 characters")
        self.assertEqual(response.context["form"]["message_to_client"].value(), note)
        self.selection.refresh_from_db()
        self.assertEqual(self.selection.status, FinishSelection.Status.DRAFT)
        self.assertFalse(mail.outbox)

    def test_missing_options_preserves_note(self):
        self.option.delete()
        response = self.client.post(
            self.url, {"message_to_client": "Keep this draft note"}
        )
        self.assertContains(response, "Add at least one option")
        self.assertContains(response, "Keep this draft note")
        self.assertFalse(mail.outbox)

    def test_client_cannot_open_or_submit_publish_form(self):
        self.client.force_login(self.client_user)
        for method in ("get", "post"):
            self.assertEqual(getattr(self.client, method)(self.url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_note_is_plain_text_and_escaped_in_notifications(self):
        note = '<script>alert("unsafe")</script>'
        self.client.post(self.url, {"message_to_client": note})
        self.assertIn(note, mail.outbox[0].body)
        self.client.force_login(self.client_user)
        response = self.client.get(reverse("projects:notification_list"))
        self.assertNotContains(response, note)
        self.assertContains(response, "&lt;script&gt;")
