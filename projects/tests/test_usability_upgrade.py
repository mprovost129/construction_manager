import uuid
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from PIL import Image

from projects.message_content import normalize_message, rendered_message
from projects.models import ChoiceImage, ConversationMessage, ConversationThread
from projects.reconciliation import project_reconciliation
from projects.selection_board import remembered_view
from projects.tests.test_choice_images import ChoiceImageTests as PhotoFixtures
from projects.tests.test_choice_images import photo
from projects.tests.test_messaging import ProjectMessagingTests as MessagingFixtures
from projects.tests.test_reconciliation import ReconciliationTests as ReportFixtures


class RichContentTests(SimpleTestCase):
    def test_allowed_formatting_and_plain_email_equivalent(self):
        body, html = normalize_message(
            "",
            '<p><strong>Cabinets</strong> <em>please</em></p><ol><li>Measure</li><li>Confirm <a href="https://example.test/spec">spec</a></li></ol>',
        )
        self.assertIn("<strong>Cabinets</strong>", html)
        self.assertIn("1. Measure", body)
        self.assertIn("2. Confirm spec (https://example.test/spec)", body)

    def test_unsafe_paste_is_removed_and_render_defends_old_content(self):
        html = '<p onclick="bad()">Safe</p><script>alert(1)</script><svg onload="bad()"></svg><img src=x onerror="bad()"><a href="javascript:bad()">Click</a><a href="//evil.test">relative</a>'
        body, cleaned = normalize_message("", html)
        for forbidden in ("onclick", "script", "svg", "<img", "javascript:", "//evil"):
            self.assertNotIn(forbidden, cleaned)
        self.assertIn("Safe", body)
        self.assertEqual(
            str(rendered_message(SimpleNamespace(body="", body_html=html))), cleaned
        )
        self.assertIn(
            "&lt;script&gt;",
            rendered_message(SimpleNamespace(body="<script>", body_html="")),
        )

    def test_blank_and_oversized_html_rejected(self):
        for value in (
            "<p><br></p>",
            "<script>bad()</script>",
            "<p>" + "x" * 10001 + "</p>",
        ):
            with self.assertRaises(ValidationError):
                normalize_message("forged fallback", value)


class RichProjectMessageTests(TestCase):
    setUpTestData = classmethod(MessagingFixtures.setUpTestData.__func__)
    create_thread = MessagingFixtures.create_thread

    def test_retry_creates_one_formatted_message_and_one_notification_batch(self):
        self.client.force_login(self.admin_user)
        url = reverse("projects:message_create", args=[self.project.pk])
        payload = dict(
            subject="Instructions",
            body="",
            body_html="<p><strong>Measure twice</strong></p>",
            submission_id=str(uuid.uuid4()),
        )
        with self.captureOnCommitCallbacks(execute=True):
            first = self.client.post(url, payload)
        self.assertEqual(first.status_code, 302)
        count = len(mail.outbox)
        with self.captureOnCommitCallbacks(execute=True):
            second = self.client.post(url, payload)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(ConversationThread.objects.count(), 1)
        self.assertEqual(ConversationMessage.objects.count(), 1)
        self.assertEqual(len(mail.outbox), count)
        item = ConversationMessage.objects.get()
        self.assertEqual(item.body, "Measure twice")
        self.assertIn("<strong>Measure twice</strong>", item.body_html)
        self.assertTrue(mail.outbox)
        self.assertIn(
            "<strong>Measure twice</strong>", mail.outbox[0].alternatives[0].content
        )
        self.assertContains(
            self.client.get(first.url), "<strong>Measure twice</strong>", html=True
        )

    def test_reused_submission_cannot_redirect_to_other_project(self):
        self.client.force_login(self.admin_user)
        payload = dict(subject="First", body="Message", submission_id=str(uuid.uuid4()))
        self.client.post(
            reverse("projects:message_create", args=[self.project.pk]), payload
        )
        response = self.client.post(
            reverse("projects:message_create", args=[self.other_project.pk]), payload
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ConversationThread.objects.count(), 1)


class PreferenceTests(SimpleTestCase):
    def test_preferences_reset_and_preview_are_separate(self):
        request = SimpleNamespace(
            user=SimpleNamespace(pk=3),
            GET={
                "group": "location",
                "view": "cards",
                "status": "open",
                "q": "temporary",
            },
            session={},
        )
        project = SimpleNamespace(pk=7)
        remembered_view(request, project)
        request.GET = {}
        self.assertEqual(
            remembered_view(request, project),
            {"group": "location", "view": "cards", "status": "open"},
        )
        request.client_preview_active = True
        self.assertEqual(remembered_view(request, project), {})
        request.client_preview_active = False
        request.GET = {"reset": "1"}
        self.assertEqual(remembered_view(request, project), {})
        request.GET = {}
        self.assertEqual(remembered_view(request, project), {})


class PreviewTests(TestCase):
    setUp = PhotoFixtures.setUp

    def test_preview_cache_stays_private_and_original_is_unchanged(self):
        cache.clear()
        image = ChoiceImage.objects.create(option=self.option, image=photo())
        url = image.get_absolute_url()
        original = image.image.read()
        image.image.close()
        response = self.client.get(url, {"thumb": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Content-Type"], "image/jpeg")
        with Image.open(BytesIO(response.content)) as preview:
            self.assertLessEqual(max(preview.size), 480)
        with patch(
            "projects.image_previews.Image.open",
            side_effect=AssertionError("Cached preview should not decode again"),
        ):
            self.assertEqual(
                self.client.get(url, {"thumb": "1"}).content, response.content
            )
        with image.image.open("rb") as source:
            self.assertEqual(source.read(), original)
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(url, {"thumb": "1"}).status_code, 404)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(url, {"thumb": "1"}).status_code, 404)


class TraceabilityTests(TestCase):
    setUp = ReportFixtures.setUp
    selection = ReportFixtures.selection
    order = ReportFixtures.order

    def test_customer_drilldown_and_csv_use_only_customer_prices(self):
        selection = self.selection("Cabinetry", "18000", "20000")
        order = self.order("-500", source=selection)
        self.client.force_login(self.customer)
        response = self.client.get(self.url)
        self.assertContains(
            response,
            reverse("projects:selection_detail", args=[self.project.pk, selection.pk]),
        )
        self.assertContains(
            response,
            reverse("projects:change_order_detail", args=[self.project.pk, order.pk]),
        )
        self.assertNotContains(response, "PRIVATE VENDOR")
        report = response.context["report"]
        detail = report["sections"][0]["details"][0]
        self.assertEqual(detail["net"], Decimal("-1500"))
        self.assertEqual(report["review_warnings"], [])
        csv = self.client.get(self.url, {"download": "csv"}).content.decode()
        self.assertIn("Supporting selections", csv)
        self.assertNotIn("87654", csv)

    def test_stale_link_is_flagged_without_rewriting_recorded_amounts(self):
        selection = self.selection("Cabinetry", "18000", "20000")
        self.order("-1500", source=selection)
        selection.chosen_option.price = Decimal("19500")
        selection.chosen_option.save()
        report = project_reconciliation(self.project)
        self.assertEqual(len(report["review_warnings"]), 1)
        self.assertEqual(report["approved_total"], Decimal("-1500"))
        self.assertEqual(report["linked_total"], Decimal("-1500"))

    def test_archived_source_warns_and_pending_credit_does_not_deduct(self):
        from django.utils import timezone

        selection = self.selection("Cabinetry", "18000", "20000")
        self.order("-500", source=selection)
        selection.archived_at = timezone.now()
        selection.save()
        report = project_reconciliation(self.project)
        self.assertEqual(len(report["review_warnings"]), 1)
        self.assertEqual(report["linked_total"], 0)


class RichStaffEmailTests(TestCase):
    from projects.tests.test_staff_messaging import StaffMessagingTests as Fixtures

    setUp = Fixtures.setUp
    user = Fixtures.user
    compose_url = Fixtures.compose_url
    data = Fixtures.data
    create_thread = Fixtures.create_thread
    detail_url = Fixtures.detail_url

    def test_email_and_browser_share_safe_formatting_with_private_recipients(self):
        from projects.staff_messaging import process_email_queue

        thread = self.create_thread(
            body="",
            body_html='<p><strong>Confirm scope</strong></p><ul><li>Kitchen</li></ul><img src="https://tracker.test/x">',
        )
        process_email_queue()
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, [self.receiver.email])
        self.assertIn("- Kitchen", email.body)
        self.assertIn("<strong>Confirm scope</strong>", email.alternatives[0].content)
        self.assertNotIn("tracker.test", email.alternatives[0].content)
        self.client.force_login(self.customer)
        self.assertIn(self.client.get(self.detail_url(thread)).status_code, (403, 404))
