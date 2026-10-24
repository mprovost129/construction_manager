import hashlib
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
    DocumentSignatureEvent,
    DocumentSignatureRequest,
    Organization,
    OrganizationMembership,
    Project,
    ProjectDocument,
    ProjectDocumentVersion,
    ProjectMembership,
)
from projects.storage import private_document_storage
from projects.tests import grant_internal_access


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
        grant_internal_access(cls.staff_user, cls.project, cls.other_project)
        grant_internal_access(
            cls.accountant,
            cls.project,
            cls.other_project,
            can_manage=False,
            can_invite_clients=False,
        )

    def upload(self, name='plan.pdf', content=b'%PDF-1.4 test'):
        return SimpleUploadedFile(name, content, content_type='application/pdf')

    def create_document(
        self,
        *,
        client_visible=True,
        approval=True,
        title='Foundation plan',
        description='',
        category=ProjectDocument.Category.PLAN,
        filename='plan.pdf',
        project=None,
    ):
        document = ProjectDocument.objects.create(
            project=project or self.project,
            title=title,
            description=description,
            category=category,
            client_visible=client_visible,
            requires_client_approval=approval,
            created_by=self.admin_user,
        )
        version = ProjectDocumentVersion.objects.create(
            document=document,
            version_number=1,
            file=self.upload(filename),
            original_filename=filename,
            uploaded_by=self.admin_user,
        )
        return document, version

    def test_document_list_searches_title_description_and_filename(self):
        target, _ = self.create_document(
            title='Second floor elevations',
            description='Issued for millwork coordination.',
            filename='elevation-set.pdf',
        )
        self.create_document(title='Foundation details')
        self.client.force_login(self.staff_user)
        url = reverse('projects:document_list', args=(self.project.pk,))

        for search in ('Second floor', 'millwork coordination', 'elevation-set'):
            with self.subTest(search=search):
                response = self.client.get(url, {'q': search})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(list(response.context['documents']), [target])
                self.assertContains(response, 'Issued for millwork coordination')

    def test_document_list_filters_category_and_internal_visibility(self):
        shared_plan, _ = self.create_document(title='Shared plan')
        internal_contract, _ = self.create_document(
            title='Internal contract',
            category=ProjectDocument.Category.CONTRACT,
            client_visible=False,
            approval=False,
        )
        self.client.force_login(self.admin_user)
        url = reverse('projects:document_list', args=(self.project.pk,))

        category_response = self.client.get(
            url,
            {'category': ProjectDocument.Category.CONTRACT},
        )
        self.assertEqual(
            list(category_response.context['documents']),
            [internal_contract],
        )

        visibility_response = self.client.get(url, {'visibility': 'shared'})
        self.assertEqual(
            list(visibility_response.context['documents']),
            [shared_plan],
        )

    def test_client_search_cannot_reveal_internal_document(self):
        self.create_document(
            title='Internal bid comparison',
            description='Confidential vendor analysis blue-spruce.',
            client_visible=False,
            approval=False,
        )
        self.client.force_login(self.client_user)
        response = self.client.get(
            reverse('projects:document_list', args=(self.project.pk,)),
            {'q': 'blue-spruce', 'visibility': 'internal'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['documents']), [])
        self.assertEqual(response.context['document_visibility'], '')
        self.assertContains(response, 'No documents match these filters')

    def test_document_list_paginates_and_keeps_filters(self):
        for number in range(21):
            self.create_document(
                title=f'Permit drawing {number:02d}',
                approval=False,
            )
        self.client.force_login(self.staff_user)
        url = reverse('projects:document_list', args=(self.project.pk,))

        first_page = self.client.get(
            url,
            {'q': 'Permit', 'category': ProjectDocument.Category.PLAN},
        )
        self.assertEqual(len(first_page.context['documents']), 20)
        self.assertContains(first_page, 'Page 1 of 2')
        self.assertContains(first_page, 'q=Permit&amp;category=plan&amp;page=2')

        second_page = self.client.get(
            url,
            {
                'q': 'Permit',
                'category': ProjectDocument.Category.PLAN,
                'page': 2,
            },
        )
        self.assertEqual(len(second_page.context['documents']), 1)

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

    def test_client_uploads_project_file_and_notifies_assigned_internal_users(self):
        self.client.force_login(self.client_user)
        response = self.client.post(
            reverse('projects:client_upload', args=(self.project.pk,)),
            {
                'title': 'Existing kitchen condition',
                'description': 'Photo before demolition.',
                'file': SimpleUploadedFile(
                    'kitchen.webp', b'webp image data', content_type='image/webp'
                ),
            },
        )

        document = ProjectDocument.objects.get()
        self.assertRedirects(
            response,
            reverse(
                'projects:document_detail', args=(self.project.pk, document.pk)
            ),
        )
        self.assertEqual(document.category, ProjectDocument.Category.CLIENT_UPLOAD)
        self.assertTrue(document.client_visible)
        self.assertFalse(document.requires_client_approval)
        self.assertEqual(document.created_by, self.client_user)
        self.assertEqual(
            mail.outbox[0].to,
            ['admin@example.com', 'staff@example.com'],
        )
        self.assertTrue(
            ActivityEvent.objects.filter(
                project=self.project,
                event_type=ActivityEvent.Type.CLIENT_UPLOAD_CREATED,
                actor=self.client_user,
            ).exists()
        )

    def test_internal_user_cannot_use_client_upload_endpoint(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(
            reverse('projects:client_upload', args=(self.project.pk,))
        )

        self.assertEqual(response.status_code, 403)

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

    def test_required_approvals_threshold_needs_multiple_distinct_clients(self):
        second_client = get_user_model().objects.create_user(
            'second-client@example.com', 'password'
        )
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=second_client,
            role=OrganizationMembership.Role.CLIENT,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=second_client,
            role=OrganizationMembership.Role.CLIENT,
        )
        document, _version = self.create_document()
        document.required_approvals = 2
        document.save(update_fields=['required_approvals'])
        decision_url = reverse(
            'projects:document_decision', args=(self.project.pk, document.pk)
        )
        list_url = reverse('projects:document_list', args=(self.project.pk,))

        self.client.force_login(self.client_user)
        self.client.post(decision_url, {'decision': DocumentDecision.Decision.APPROVED})
        self.client.force_login(self.staff_user)
        listed = next(
            d for d in self.client.get(list_url).context['documents']
            if d.pk == document.pk
        )
        self.assertIn('1 of 2', listed.approval_status)

        self.client.force_login(second_client)
        self.client.post(decision_url, {'decision': DocumentDecision.Decision.APPROVED})
        self.client.force_login(self.staff_user)
        listed = next(
            d for d in self.client.get(list_url).context['documents']
            if d.pk == document.pk
        )
        self.assertEqual(listed.approval_status, 'Approved')

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

    def test_manager_requests_authenticated_client_signature(self):
        document, version = self.create_document(approval=False)
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse(
                'projects:document_signature_request',
                args=(self.project.pk, document.pk, version.pk),
            ),
            {
                'signer': self.client_user.pk,
                'message': 'Please sign before work begins.',
            },
            REMOTE_ADDR='203.0.113.10',
            HTTP_USER_AGENT='Signature test browser',
        )

        signature_request = DocumentSignatureRequest.objects.get()
        self.assertRedirects(
            response,
            reverse(
                'projects:document_detail', args=(self.project.pk, document.pk)
            ),
        )
        self.assertEqual(signature_request.signer, self.client_user)
        self.assertEqual(
            signature_request.document_sha256,
            hashlib.sha256(b'%PDF-1.4 test').hexdigest(),
        )
        self.assertEqual(signature_request.events.count(), 1)
        event = signature_request.events.get()
        self.assertEqual(event.event_type, DocumentSignatureEvent.Type.REQUESTED)
        self.assertEqual(event.ip_address, '203.0.113.10')
        self.assertEqual(mail.outbox[0].to, ['client@example.com'])
        self.assertIn('Please sign before work begins.', mail.outbox[0].body)
        self.assertTrue(
            ActivityEvent.objects.filter(
                project=self.project,
                event_type=ActivityEvent.Type.DOCUMENT_SIGNATURE_REQUESTED,
            ).exists()
        )

    def test_named_client_can_review_sign_and_download_certificate(self):
        document, version = self.create_document(approval=False)
        signature_request = DocumentSignatureRequest.objects.create(
            version=version,
            signer=self.client_user,
            requested_by=self.admin_user,
            document_sha256=hashlib.sha256(b'%PDF-1.4 test').hexdigest(),
            consent_text='Test consent text.',
        )
        review_url = reverse(
            'projects:document_signature_review',
            args=(self.project.pk, document.pk, signature_request.pk),
        )
        self.client.force_login(self.client_user)

        get_response = self.client.get(
            review_url,
            REMOTE_ADDR='198.51.100.20',
            HTTP_USER_AGENT='Client browser',
        )
        self.assertContains(get_response, 'Review the complete PDF')
        self.assertTrue(
            signature_request.events.filter(
                event_type=DocumentSignatureEvent.Type.VIEWED
            ).exists()
        )

        response = self.client.post(
            review_url,
            {
                'action': 'sign',
                'typed_name': 'Jane Homeowner',
                'consent': 'on',
            },
            REMOTE_ADDR='198.51.100.20',
            HTTP_USER_AGENT='Client browser',
        )
        signature_request.refresh_from_db()
        self.assertRedirects(
            response,
            reverse(
                'projects:document_detail', args=(self.project.pk, document.pk)
            ),
        )
        self.assertEqual(
            signature_request.status, DocumentSignatureRequest.Status.SIGNED
        )
        self.assertEqual(signature_request.typed_name, 'Jane Homeowner')
        self.assertIsNotNone(signature_request.signed_at)
        self.assertEqual(signature_request.completed_ip, '198.51.100.20')
        self.assertTrue(
            signature_request.events.filter(
                event_type=DocumentSignatureEvent.Type.SIGNED
            ).exists()
        )
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.DOCUMENT_SIGNED,
                project=self.project,
            ).exists()
        )

        certificate_response = self.client.get(
            reverse(
                'projects:document_signature_certificate',
                args=(self.project.pk, document.pk, signature_request.pk),
            )
        )
        self.assertEqual(certificate_response.status_code, 200)
        self.assertEqual(certificate_response['Content-Type'], 'application/pdf')
        self.assertTrue(certificate_response.content.startswith(b'%PDF'))

    def test_client_cannot_open_another_clients_signature_request(self):
        other_client = get_user_model().objects.create_user(
            'other-client@example.com', 'password'
        )
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=other_client,
            role=OrganizationMembership.Role.CLIENT,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=other_client,
            role=OrganizationMembership.Role.CLIENT,
        )
        document, version = self.create_document(approval=False)
        signature_request = DocumentSignatureRequest.objects.create(
            version=version,
            signer=self.client_user,
            requested_by=self.admin_user,
            document_sha256=hashlib.sha256(b'%PDF-1.4 test').hexdigest(),
            consent_text='Test consent text.',
        )
        self.client.force_login(other_client)
        response = self.client.get(
            reverse(
                'projects:document_signature_review',
                args=(self.project.pk, document.pk, signature_request.pk),
            )
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(signature_request.events.exists())

    def test_signature_is_blocked_if_stored_document_bytes_change(self):
        document, version = self.create_document(approval=False)
        signature_request = DocumentSignatureRequest.objects.create(
            version=version,
            signer=self.client_user,
            requested_by=self.admin_user,
            document_sha256=hashlib.sha256(b'%PDF-1.4 test').hexdigest(),
            consent_text='Test consent text.',
        )
        Path(version.file.path).write_bytes(b'%PDF-1.4 altered')
        self.client.force_login(self.client_user)
        response = self.client.post(
            reverse(
                'projects:document_signature_review',
                args=(self.project.pk, document.pk, signature_request.pk),
            ),
            {
                'action': 'sign',
                'typed_name': 'Jane Homeowner',
                'consent': 'on',
            },
        )
        signature_request.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            signature_request.status, DocumentSignatureRequest.Status.PENDING
        )

    def test_manager_can_cancel_pending_signature_request(self):
        document, version = self.create_document(approval=False)
        signature_request = DocumentSignatureRequest.objects.create(
            version=version,
            signer=self.client_user,
            requested_by=self.admin_user,
            document_sha256=hashlib.sha256(b'%PDF-1.4 test').hexdigest(),
            consent_text='Test consent text.',
        )
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse(
                'projects:document_signature_cancel',
                args=(self.project.pk, document.pk, signature_request.pk),
            )
        )
        signature_request.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            signature_request.status, DocumentSignatureRequest.Status.CANCELLED
        )
        self.assertTrue(
            signature_request.events.filter(
                event_type=DocumentSignatureEvent.Type.CANCELLED
            ).exists()
        )

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
