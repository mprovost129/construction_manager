from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice, InvoiceLineItem
from billing.services import issue_invoice
from projects.forms import ClientInvitationForm
from projects.models import (
    ActivityEvent,
    InAppNotification,
    NotificationPreference,
    Organization,
    OrganizationDisclosure,
    OrganizationMembership,
    Project,
    ProjectDocument,
    ProjectDocumentVersion,
    ProjectMembership,
)
from projects.services import deliver_project_notification


class HeritageLaunchFeatureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.admin = user_model.objects.create_user(
            email='admin@example.com', password='password'
        )
        cls.client_user = user_model.objects.create_user(
            email='client@example.com', password='password'
        )
        cls.organization = Organization.objects.create(
            name='Heritage', slug='heritage'
        )
        OrganizationMembership.objects.create(
            organization=cls.organization,
            user=cls.admin,
            role=OrganizationMembership.Role.ADMIN,
        )
        OrganizationMembership.objects.create(
            organization=cls.organization,
            user=cls.client_user,
            role=OrganizationMembership.Role.CLIENT,
        )
        cls.project = Project.objects.create(
            organization=cls.organization,
            name='Test Home',
            created_by=cls.admin,
        )
        ProjectMembership.objects.create(
            project=cls.project,
            user=cls.client_user,
            role=OrganizationMembership.Role.CLIENT,
            invited_by=cls.admin,
        )

    def test_second_client_invitation_is_blocked(self):
        form = ClientInvitationForm(
            {'email': 'replacement@example.com'},
            project=self.project,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('designated client', form.errors['email'][0].lower())

    def test_notification_preferences_control_both_channels(self):
        NotificationPreference.objects.create(
            user=self.client_user,
            project=self.project,
            event_type=NotificationPreference.Event.DOCUMENTS,
            email_enabled=False,
            in_app_enabled=False,
        )
        request = RequestFactory().get('/')

        result = deliver_project_notification(
            request=request,
            project=self.project,
            event_type=NotificationPreference.Event.DOCUMENTS,
            title='New plan',
            message='A plan is ready.',
            url_path=reverse('projects:document_list', args=(self.project.pk,)),
            recipient_list=[self.client_user.email],
        )

        self.assertEqual(result, 0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(InAppNotification.objects.exists())

    def test_open_overdue_selection_uses_buildertrend_status(self):
        selection = self.project.finish_selections.create(
            number=1,
            title='Kitchen cabinets',
            due_date=timezone.localdate() - timedelta(days=1),
            status='open',
            created_by=self.admin,
            opened_by=self.admin,
            opened_at=timezone.now(),
        )

        self.assertTrue(selection.is_overdue)
        self.assertEqual(selection.buildertrend_status_label, 'Pending: Overdue')
        self.assertEqual(selection.buildertrend_status_class, 'pending')

    def test_client_login_and_page_view_are_audited(self):
        self.client.force_login(self.client_user)
        self.client.get(reverse('projects:detail', args=(self.project.pk,)))

        event_types = set(
            ActivityEvent.objects.filter(actor=self.client_user).values_list(
                'event_type', flat=True
            )
        )
        self.assertIn(ActivityEvent.Type.CLIENT_LOGGED_IN, event_types)
        self.assertIn(ActivityEvent.Type.CLIENT_VIEWED, event_types)

    def test_invoice_disclosures_are_snapshotted_when_issued(self):
        OrganizationDisclosure.objects.create(
            organization=self.organization,
            title='Payment terms',
            body='Payment is due as stated on this invoice.',
            include_on_invoices=True,
        )
        invoice = Invoice.objects.create(
            organization=self.organization,
            project=self.project,
            title='Progress billing',
            due_date=timezone.localdate() + timedelta(days=30),
            created_by=self.admin,
        )
        InvoiceLineItem.objects.create(
            invoice=invoice,
            description='Construction progress',
            quantity=1,
            unit_price='1000.00',
        )

        invoice = issue_invoice(invoice_id=invoice.pk, actor=self.admin)

        self.assertIn('Payment terms', invoice.disclosure_snapshot)
        self.assertIn('Payment is due', invoice.disclosure_snapshot)

    @override_settings(DEBUG=True, TURNSTILE_SITE_KEY='', TURNSTILE_SECRET_KEY='')
    def test_public_document_requires_verification_then_downloads(self):
        document = ProjectDocument.objects.create(
            project=self.project,
            title='Site plan',
            category=ProjectDocument.Category.PLAN,
            client_visible=True,
            created_by=self.admin,
        )
        version = ProjectDocumentVersion.objects.create(
            document=document,
            version_number=1,
            file=SimpleUploadedFile('site-plan.pdf', b'%PDF-test'),
            original_filename='site-plan.pdf',
            uploaded_by=self.admin,
            public_qr_enabled=True,
        )
        public_url = reverse('projects:public_document', args=(version.public_share_token,))
        download_url = reverse(
            'projects:public_document_download', args=(version.public_share_token,)
        )

        self.assertRedirects(self.client.get(download_url), public_url)
        response = self.client.post(public_url, {'human_verified': 'on'})
        self.assertRedirects(response, public_url)
        response = self.client.get(download_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
