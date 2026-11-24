import tempfile
from io import BytesIO
from uuid import uuid4

from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from projects.client_preview import SESSION_KEY
from projects.job_site import materials, revision
from projects.models import (
    ActivityEvent,
    ChoiceImage,
    DocumentDecision,
    JobSiteShare,
    ProjectDocument,
    ProjectDocumentVersion,
)
from projects.tests import test_reconciliation


class JobSiteTests(TestCase):
    setUpData = test_reconciliation.ReconciliationTests.setUp
    selection = test_reconciliation.ReconciliationTests.selection

    def setUp(self):
        self.setUpData()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.settings_override = override_settings(
            PRIVATE_MEDIA_ROOT=self.folder.name, PRIVATE_STORAGE_BACKEND="local"
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.client.force_login(self.admin)
        self.public = Client()
        self.manage_url = reverse("projects:job_site_manage", args=[self.project.pk])
        self.share = JobSiteShare.objects.create(project=self.project)
        self.row = self.selection("Windows", "27000", "25000")
        self.row.chosen_option.specification = "White frame, tempered glass"
        self.row.chosen_option.description = "PRIVATE PRODUCT NOTES"
        self.row.chosen_option.save()
        self.version = self.document()

    def document(self, **kwargs):
        data = dict(
            project=self.project,
            title="Approved floor plan",
            category="plan",
            client_visible=True,
            created_by=self.admin,
        )
        data.update(kwargs)
        doc = ProjectDocument.objects.create(**data)
        return ProjectDocumentVersion.objects.create(
            document=doc,
            version_number=1,
            file=ContentFile(
                b"%PDF-1.4 sample field plan", name="private-client-filename.pdf"
            ),
            original_filename="private-client-filename.pdf",
            notes="PRIVATE VERSION NOTES",
            uploaded_by=self.admin,
        )

    def payload(self, **changes):
        self.share.refresh_from_db()
        docs, rows = materials(self.project)
        data = dict(
            action="save",
            revision=revision(self.share, docs, rows),
            enabled="on",
            reviewed="on",
            documents=[str(self.version.pk)],
            selections=[str(self.row.pk)],
        )
        data.update(changes)
        return data

    def publish(self):
        self.assertRedirects(
            self.client.post(self.manage_url, self.payload()), self.manage_url
        )
        self.share.refresh_from_db()
        self.public_url = reverse("projects:job_site_public", args=[self.share.token])
        self.doc_url = reverse(
            "projects:job_site_document", args=[self.share.token, self.version.pk]
        )
        return self.public_url

    def test_disabled_by_default_and_get_does_not_publish(self):
        self.assertEqual(
            self.client.get(self.manage_url)["Referrer-Policy"], "same-origin"
        )
        self.assertContains(self.client.get(self.manage_url), "Job-site access is off")
        url = reverse("projects:job_site_public", args=[self.share.token])
        self.assertEqual(self.public.get(url).status_code, 404)
        self.assertEqual(
            self.public.get(
                reverse("projects:job_site_public", args=[uuid4()])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("projects:job_site_qr", args=[self.project.pk])
            ).status_code,
            404,
        )

    def test_no_login_packet_contains_only_allowlisted_material(self):
        self.publish()
        page = self.public.get(self.public_url)
        for value in (
            "Approved floor plan",
            "White frame, tempered glass",
            "Client choice",
        ):
            self.assertContains(page, value)
        for value in (
            "27000",
            "25000",
            "87654",
            "PRIVATE",
            "private-client-filename",
            "admin@example",
            "client@example",
            "Account security",
            "Allowances",
            "Staff messages",
        ):
            self.assertNotContains(page, value)
        self.assertEqual(page["Referrer-Policy"], "no-referrer")
        self.assertIn("no-store", page["Cache-Control"])
        self.assertIn("noindex", page["X-Robots-Tag"])
        file = self.public.get(self.doc_url)
        self.assertEqual(file.status_code, 200)
        self.assertTrue(file.streaming)
        self.assertNotIn("private-client", file["Content-Disposition"])
        self.assertEqual(
            b"".join(file.streaming_content), b"%PDF-1.4 sample field plan"
        )
        file.close()

    def test_unshared_foreign_and_financial_documents_cannot_be_requested(self):
        internal = self.document(title="Private spreadsheet", client_visible=False)
        contract = self.document(title="Private contract", category="contract")
        foreign = self.document(title="Other home plan", project=self.foreign)
        unshared = self.document(title="Not selected for site")
        for item in (internal, contract, foreign):
            result = self.client.post(
                self.manage_url, self.payload(documents=[str(item.pk)])
            )
            self.assertContains(result, "Select a valid choice")
        self.publish()
        for item in (internal, contract, foreign, unshared):
            url = reverse(
                "projects:job_site_document", args=[self.share.token, item.pk]
            )
            self.assertEqual(self.public.get(url).status_code, 404)
            self.assertNotContains(
                self.public.get(self.public_url), item.document.title
            )

    def test_client_approval_is_enforced_and_decline_removes_access(self):
        doc = self.version.document
        doc.requires_client_approval, doc.required_approvals = True, 2
        doc.save()
        self.assertEqual(materials(self.project)[0], [])
        decision = DocumentDecision.objects.create(
            version=self.version, decided_by=self.customer, decision="approved"
        )
        self.assertEqual(materials(self.project)[0], [])
        DocumentDecision.objects.create(
            version=self.version, decided_by=self.admin, decision="approved"
        )
        self.publish()
        decision.decision = "declined"
        decision.save()
        self.assertEqual(self.public.get(self.doc_url).status_code, 404)

    def test_new_document_version_is_not_automatically_public(self):
        self.publish()
        new = ProjectDocumentVersion.objects.create(
            document=self.version.document,
            version_number=2,
            file=ContentFile(b"%PDF replacement", name="replacement.pdf"),
            original_filename="replacement.pdf",
            uploaded_by=self.admin,
        )
        self.assertEqual(self.public.get(self.doc_url).status_code, 404)
        self.assertEqual(
            self.public.get(
                reverse("projects:job_site_document", args=[self.share.token, new.pk])
            ).status_code,
            404,
        )
        self.assertNotContains(self.public.get(self.public_url), "Approved floor plan")
        self.version = new
        self.publish()
        self.assertContains(self.public.get(self.public_url), "Version 2")

    def test_hidden_document_and_archived_selection_are_removed(self):
        self.publish()
        doc = self.version.document
        doc.client_visible = False
        doc.save()
        self.row.archived_at = timezone.now()
        self.row.archived_by = self.admin
        self.row.save()
        self.assertEqual(self.public.get(self.doc_url).status_code, 404)
        self.assertNotContains(self.public.get(self.public_url), "Client choice")

    def test_only_selected_rows_and_same_project_options_allowed(self):
        for status in ("draft", "open", "voided", "imported"):
            row = self.selection(status, "100", "0", status=status)
            page = self.client.post(
                self.manage_url, self.payload(selections=[str(row.pk)])
            )
            self.assertContains(page, "Select a valid choice")
        foreign = self.selection("foreign", "100", "0", project=self.foreign)
        self.assertContains(
            self.client.post(
                self.manage_url, self.payload(selections=[str(foreign.pk)])
            ),
            "Select a valid choice",
        )
        self.publish()
        self.row.chosen_option = foreign.chosen_option
        self.row.save()
        self.assertNotContains(self.public.get(self.public_url), "Client choice")

    def test_review_confirmation_and_stale_forms(self):
        self.assertContains(
            self.client.post(self.manage_url, self.payload(reviewed="")),
            "Review the materials",
        )
        self.assertContains(
            self.client.post(
                self.manage_url, self.payload(documents=[], selections=[])
            ),
            "Choose at least one",
        )
        data = self.payload()
        self.row.chosen_option.specification = "Changed specification"
        self.row.chosen_option.save()
        self.assertContains(
            self.client.post(self.manage_url, data),
            "Materials or sharing settings changed",
        )
        self.publish()
        self.assertContains(
            self.client.post(self.manage_url, data),
            "Materials or sharing settings changed",
        )
        self.assertTrue(
            ActivityEvent.objects.filter(
                project=self.project, summary__contains="Job-site sharing"
            ).exists()
        )

    def test_changed_selection_requires_review_without_reprinting_code(self):
        self.publish()
        token = self.share.token
        self.row.chosen_option.specification = "Updated product"
        self.row.chosen_option.save()
        self.assertNotContains(self.public.get(self.public_url), "Updated product")
        self.assertNotContains(self.public.get(self.public_url), "White frame")
        self.assertContains(
            self.client.get(self.manage_url), "1 previously shared material"
        )
        self.publish()
        self.assertEqual(self.share.token, token)
        self.assertContains(self.public.get(self.public_url), "Updated product")

    def test_photos_are_pinned_revocable_and_cross_project_safe(self):
        output = BytesIO()
        Image.new("RGB", (20, 20), "green").save(output, format="PNG")
        option = self.row.chosen_option
        option.image.save("product.png", ContentFile(output.getvalue()))
        photo = ChoiceImage.objects.create(
            option=option, image=ContentFile(output.getvalue(), name="view.png")
        )
        self.publish()
        urls = [
            reverse("projects:job_site_photo", args=[self.share.token, self.row.pk]),
            reverse(
                "projects:job_site_gallery",
                args=[self.share.token, self.row.pk, photo.pk],
            ),
        ]
        for url in urls:
            response = self.public.get(url + "?thumb=1")
            self.assertEqual(response.status_code, 200)
            self.assertIn("image/", response["Content-Type"])
            response.close()
        self.assertEqual(
            self.public.get(
                reverse(
                    "projects:job_site_gallery",
                    args=[self.share.token, self.row.pk, photo.pk + 1],
                )
            ).status_code,
            404,
        )
        self.client.post(self.manage_url, self.payload(action="disable"))
        for url in urls:
            self.assertEqual(self.public.get(url).status_code, 404)

    def test_disable_and_rotation_revoke_old_links_and_downloads(self):
        self.publish()
        old_url, old_file = self.public_url, self.doc_url
        self.client.post(self.manage_url, self.payload(action="rotate"))
        self.assertEqual(self.public.get(old_url).status_code, 404)
        self.assertEqual(self.public.get(old_file).status_code, 404)
        self.publish()
        self.assertNotEqual(self.public_url, old_url)
        self.client.post(self.manage_url, self.payload(action="disable"))
        self.publish()
        self.assertEqual(self.public.get(old_url).status_code, 404)

    def test_delete_and_restore_do_not_reactivate_public_access(self):
        self.publish()
        old = self.public_url
        self.client.post(
            reverse("projects:delete", args=[self.project.pk]),
            {"name": self.project.name},
        )
        self.assertEqual(self.public.get(old).status_code, 404)
        self.client.post(
            reverse("projects:restore", args=[self.project.pk]),
            {"name": self.project.name},
        )
        self.assertEqual(self.public.get(old).status_code, 404)
        self.share.refresh_from_db()
        self.assertFalse(self.share.enabled)

    def test_staff_permissions_and_client_preview_apply_to_all_controls(self):
        self.publish()
        urls = [
            self.manage_url,
            reverse("projects:job_site_qr", args=[self.project.pk]),
            reverse("projects:job_site_sign", args=[self.project.pk]),
        ]
        for url in urls:
            self.assertEqual(self.public.get(url).status_code, 302)
            for user in (self.customer, self.office):
                self.client.force_login(user)
                self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        session = self.client.session
        session[SESSION_KEY] = self.project.pk
        session.save()
        self.assertEqual(self.client.get(self.manage_url).status_code, 403)

    def test_public_routes_are_read_only_and_staff_posts_require_csrf(self):
        self.publish()
        for url in (self.public_url, self.doc_url):
            self.assertEqual(self.public.post(url, {}).status_code, 405)
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.admin)
        self.assertEqual(browser.post(self.manage_url, self.payload()).status_code, 403)

    def test_qr_is_local_png_and_sign_printable(self):
        self.publish()
        qr = self.client.get(
            reverse("projects:job_site_qr", args=[self.project.pk]) + "?download=1"
        )
        self.assertEqual(qr.status_code, 200)
        self.assertTrue(qr.content.startswith(b"\x89PNG"))
        self.assertIn("attachment", qr["Content-Disposition"])
        sign = self.client.get(
            reverse("projects:job_site_sign", args=[self.project.pk])
        )
        self.assertContains(sign, "Print sign / Save as PDF")
        self.assertNotContains(sign, "api.qrserver")

    def test_text_is_escaped_and_normal_downloads_still_require_login(self):
        self.row.chosen_option.specification = "<script>alert(1)</script>"
        self.row.chosen_option.save()
        self.publish()
        self.assertContains(self.public.get(self.public_url), "&lt;script&gt;")
        self.assertNotContains(self.public.get(self.public_url), "<script>")
        normal = reverse(
            "projects:document_download",
            args=[self.project.pk, self.version.document_id, self.version.pk],
        )
        self.assertEqual(self.public.get(normal).status_code, 302)
