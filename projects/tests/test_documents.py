import shutil
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from projects.models import (
    ActivityEvent,
    DocumentDecision,
    Organization,
    OrganizationMembership,
    Project,
    ProjectDocument,
    ProjectDocumentVersion,
    ProjectMembership,
)
from projects.storage import private_document_storage


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ProjectDocumentTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_media_directory = Path(tempfile.mkdtemp(prefix='project-documents-'))
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
            name='Example Builders', slug='example-builders'
        )
        cls.project = Project.objects.create(
            organization=cls.organization,
            name='Oak Street',
            status=Project.Status.ACTIVE,
        )
        cls.other_project = Project.objects.create(
            organization=cls.organization, name='Pine Street'
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
                organization=cls.organization, user=user, role=role
            )
        for user, role in (
            (cls.client_user, OrganizationMembership.Role.CLIENT),
            (cls.subcontractor, OrganizationMembership.Role.SUBCONTRACTOR),
        ):
            ProjectMembership.objects.create(
                project=cls.project, user=user, role=role
            )

    def upload(self, name='plan.pdf', content=b'%PDF-1.4 test'):
        return SimpleUploadedFile(name, content, content_type='application/pdf')

    def create_document(self, *, client_visible=True, approval=True):
        document = ProjectDocument.objects.create(
            project=self.project,
            title='Foundation plan',
            category=ProjectDocument.Category.PLAN,
            client_visible=client_visible,
            requires_client_approval=approval,
            created_by=self.admin_user,
        )
        version = ProjectDocumentVersion.objects.create(
            document=document,
            version_number=1,
            file=self.upload(),
            original_filename='plan.pdf',
            uploaded_by=self.admin_user,
        )
        return document, version

    def test_admin_uploads_private_document_and_notifies_client(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse('projects:document_create', args=(self.project.pk,)),
            {
                'title': 'Foundation plan',
                'category': ProjectDocument.Category.PLAN,
                'client_visible': 'on',
                'requires_client_approval': 'on',
                'file': self.upload(),
                'version_notes': 'Issued for client review.',
            },
        )

        document = ProjectDocument.objects.get()
        version = document.versions.get()
        self.assertRedirects(
            response,
            reverse(
                'projects:document_detail', args=(self.project.pk, document.pk)
            ),
        )
        self.assertEqual(version.version_number, 1)
        self.assertTrue(Path(version.file.path).is_file())
        self.assertTrue(
            str(Path(version.file.path).resolve()).startswith(
                str(self.private_media_directory.resolve())
            )
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['client@example.com'])
        self.assertTrue(
            ActivityEvent.objects.filter(
                project=self.project,
                event_type=ActivityEvent.Type.DOCUMENT_CREATED,
            ).exists()
        )

    def test_internal_document_is_hidden_from_client(self):
        document, version = self.create_document(
            client_visible=False, approval=False
        )
        self.client.force_login(self.client_user)

        list_response = self.client.get(
            reverse('projects:document_list', args=(self.project.pk,))
        )
        detail_response = self.client.get(
            reverse(
                'projects:document_detail', args=(self.project.pk, document.pk)
            )
        )
        download_response = self.client.get(
            reverse(
                'projects:document_download',
                args=(self.project.pk, document.pk, version.pk),
            )
        )

        self.assertNotContains(list_response, document.title)
        self.assertEqual(detail_response.status_code, 404)
        self.assertEqual(download_response.status_code, 404)

    def test_authorized_client_can_download_shared_document(self):
        document, version = self.create_document()
        self.client.force_login(self.client_user)

        detail_response = self.client.get(
            reverse(
                'projects:document_detail', args=(self.project.pk, document.pk)
            )
        )
        response = self.client.get(
            reverse(
                'projects:document_download',
                args=(self.project.pk, document.pk, version.pk),
            )
        )

        self.assertContains(detail_response, document.title)
        self.assertContains(detail_response, 'Record decision')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'%PDF-1.4 test')
        self.assertIn('attachment;', response['Content-Disposition'])

    def test_accountant_and_subcontractor_cannot_access_documents(self):
        url = reverse('projects:document_list', args=(self.project.pk,))
        for user in (self.accountant, self.subcontractor):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_client_decision_is_audited_and_notifies_admin_and_staff(self):
        document, version = self.create_document()
        self.client.force_login(self.client_user)
        response = self.client.post(
            reverse(
                'projects:document_decision', args=(self.project.pk, document.pk)
            ),
            {
                'decision': DocumentDecision.Decision.APPROVED,
                'comment': 'Approved as shown.',
            },
        )

        decision = DocumentDecision.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(decision.version, version)
        self.assertEqual(decision.decided_by, self.client_user)
        self.assertEqual(
            mail.outbox[0].to, ['admin@example.com', 'staff@example.com']
        )
        self.assertTrue(
            ActivityEvent.objects.filter(
                project=self.project,
                event_type=ActivityEvent.Type.DOCUMENT_DECISION_RECORDED,
                actor=self.client_user,
            ).exists()
        )

        self.client.post(
            reverse(
                'projects:document_decision', args=(self.project.pk, document.pk)
            ),
            {'decision': DocumentDecision.Decision.DECLINED},
        )
        self.assertEqual(DocumentDecision.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_internal_user_cannot_record_client_decision(self):
        document, _ = self.create_document()
        self.client.force_login(self.staff_user)
        response = self.client.post(
            reverse(
                'projects:document_decision', args=(self.project.pk, document.pk)
            ),
            {'decision': DocumentDecision.Decision.APPROVED},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(DocumentDecision.objects.exists())

    def test_new_version_requires_a_new_client_decision(self):
        document, first_version = self.create_document()
        DocumentDecision.objects.create(
            version=first_version,
            decided_by=self.client_user,
            decision=DocumentDecision.Decision.APPROVED,
        )
        self.client.force_login(self.staff_user)
        response = self.client.post(
            reverse(
                'projects:document_version_create',
                args=(self.project.pk, document.pk),
            ),
            {'file': self.upload('revised-plan.pdf'), 'notes': 'Revised dimensions.'},
        )

        new_version = document.versions.first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(new_version.version_number, 2)
        self.assertFalse(new_version.decisions.exists())
        self.assertEqual(first_version.decisions.count(), 1)

    def test_cross_project_download_is_not_found(self):
        document, version = self.create_document()
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse(
                'projects:document_download',
                args=(self.other_project.pk, document.pk, version.pk),
            )
        )
        self.assertEqual(response.status_code, 404)

    def test_unsupported_file_type_is_rejected(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse('projects:document_create', args=(self.project.pk,)),
            {
                'title': 'Executable',
                'category': ProjectDocument.Category.OTHER,
                'client_visible': 'on',
                'file': self.upload('malware.exe', b'not executable'),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Unsupported file type')
        self.assertFalse(ProjectDocument.objects.exists())
