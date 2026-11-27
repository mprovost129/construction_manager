import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from projects.client_preview import SESSION_KEY
from projects.models import (ActivityEvent, Organization, OrganizationMembership, Project,
    ProjectMembership, FinishSelection, SelectionOption, ProjectDocument, ProjectDocumentVersion,
    DocumentSignatureRequest, DocumentSignatureEvent, NotificationPreference, ConversationThread)
from projects.tests import grant_internal_access


class ClientPreviewTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        setting = override_settings(PRIVATE_MEDIA_ROOT=Path(self.media.name))
        setting.enable()
        self.addCleanup(setting.disable)
        self.org = Organization.objects.create(name='Builder', slug='builder')
        self.other_org = Organization.objects.create(name='Other', slug='other')
        self.staff = get_user_model().objects.create_user('staff@example.test', 'password')
        self.customer = get_user_model().objects.create_user('customer@example.test', 'password')
        self.staff_membership = OrganizationMembership.objects.create(organization=self.org, user=self.staff, role='staff')
        OrganizationMembership.objects.create(organization=self.org, user=self.customer, role='client')
        self.project = Project.objects.create(organization=self.org, name='Client home', description='CONFIDENTIAL COMPANY NOTES')
        self.other_project = Project.objects.create(organization=self.org, name='Unassigned home')
        self.foreign = Project.objects.create(organization=self.other_org, name='Other company')
        grant_internal_access(self.staff, self.project, can_manage=False, can_invite_clients=False)
        self.client_membership = ProjectMembership.objects.create(project=self.project, user=self.customer, role='client')
        ProjectMembership.objects.create(project=self.other_project, user=self.customer, role='client')
        self.selection = FinishSelection.objects.create(project=self.project, number=1, title='Shared selection',
            allowance_amount=1000, created_by=self.staff, status='open', opened_by=self.staff, opened_at=timezone.now())
        self.option = SelectionOption.objects.create(selection=self.selection, name='Client option', price=1200, cost=987.65)
        self.draft = FinishSelection.objects.create(project=self.project, number=2, title='PRIVATE DRAFT', created_by=self.staff)
        self.document = ProjectDocument.objects.create(project=self.project, title='Shared PDF', created_by=self.staff, client_visible=True)
        self.version = ProjectDocumentVersion.objects.create(document=self.document, version_number=1,
            uploaded_by=self.staff, original_filename='shared.pdf', file=SimpleUploadedFile('shared.pdf', b'%PDF-1.4 test'))
        self.private_document = ProjectDocument.objects.create(project=self.project, title='PRIVATE DOCUMENT', created_by=self.staff, client_visible=False)
        self.start = reverse('projects:client_preview_start')
        self.stop = reverse('projects:client_preview_stop')
        self.detail = reverse('projects:detail', args=[self.project.pk])
        self.client.force_login(self.staff)

    def enter(self):
        response = self.client.post(self.start, {'project': self.project.pk})
        self.assertRedirects(response, self.detail, fetch_redirect_response=False)

    def test_staff_switch_and_return_preserve_login_and_permissions(self):
        self.assertContains(self.client.get(self.detail), 'View as client')
        self.enter()
        response = self.client.get(self.detail)
        self.assertContains(response, 'Back to staff view')
        self.assertContains(response, 'Primary client access')
        self.assertNotContains(response, 'CONFIDENTIAL COMPANY NOTES')
        self.assertNotContains(response, '>Company</a>')
        self.assertNotContains(response, '>QuickBooks</a>')
        self.assertNotContains(response, '>Templates</a>')
        self.assertEqual(self.client.session['_auth_user_id'], str(self.staff.pk))
        self.assertFalse(response.context['viewer_is_internal'])
        self.assertEqual(response.wsgi_request.user.pk, self.customer.pk)
        self.assertEqual(self.client.get(reverse('core:home')).url, self.detail)
        self.assertEqual(self.client.post(self.stop).status_code, 302)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertContains(self.client.get(self.detail), 'CONFIDENTIAL COMPANY NOTES')
        self.assertEqual(self.client.session['_auth_user_id'], str(self.staff.pk))
        events = ActivityEvent.objects.filter(event_type__in=['client_preview_started', 'client_preview_stopped'])
        self.assertEqual(events.count(), 2)
        self.assertEqual(set(events.values_list('actor_id', flat=True)), {self.staff.pk})

    def test_all_internal_roles_can_preview_only_assigned_projects(self):
        for role in OrganizationMembership.INTERNAL_ROLES:
            with self.subTest(role=role):
                self.staff_membership.role = role
                self.staff_membership.save()
                self.assertContains(self.client.get(self.start), 'Switch to client view')
                self.enter()
                self.assertContains(self.client.get(self.detail), 'Primary client access')
                self.client.post(self.stop)
        self.staff_membership.role = 'staff'
        self.staff_membership.save()
        for project in (self.other_project, self.foreign):
            response = self.client.post(self.start, {'project': project.pk})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(SESSION_KEY, self.client.session)

    def test_clients_subcontractors_and_anonymous_never_get_switcher(self):
        for role in ('client', 'subcontractor'):
            self.staff_membership.role = role
            self.staff_membership.save()
            self.assertNotContains(self.client.get(reverse('core:home')), 'View as client')
            self.assertEqual(self.client.get(self.start).status_code, 403)
            self.assertEqual(self.client.post(self.start, {'project': self.project.pk}).status_code, 403)
        self.client.logout()
        self.assertNotContains(self.client.get(reverse('core:home')), 'View as client')
        self.assertEqual(self.client.get(self.start).status_code, 302)

    def test_preview_hides_internal_data_and_matches_real_client_content(self):
        self.enter()
        selection_url = reverse('projects:selection_detail', args=[self.project.pk, self.selection.pk])
        response = self.client.get(selection_url)
        self.assertContains(response, 'Client option')
        self.assertContains(response, '1200.00')
        self.assertNotContains(response, '987.65')
        self.assertNotContains(response, 'Estimated cost')
        self.assertNotContains(response, 'Estimated margin')
        self.assertEqual(self.client.get(reverse('projects:selection_detail', args=[self.project.pk, self.draft.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('projects:document_list', args=[self.project.pk])), 'PRIVATE DOCUMENT')
        self.assertEqual(self.client.get(reverse('projects:document_detail', args=[self.project.pk, self.private_document.pk])).status_code, 404)
        download = self.client.get(reverse('projects:document_download', args=[self.project.pk, self.document.pk, self.version.pk]))
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b''.join(download.streaming_content), b'%PDF-1.4 test')
        download.close()
        customer_browser = Client()
        customer_browser.force_login(self.customer)
        actual = customer_browser.get(selection_url)
        self.assertEqual(list(response.context['selection_options']), list(actual.context['selection_options']))
        self.assertEqual(response.context['can_choose'], actual.context['can_choose'])

    def test_post_put_patch_delete_and_cross_project_or_staff_urls_are_blocked(self):
        self.enter()
        choose = reverse('projects:selection_choose', args=[self.project.pk, self.selection.pk])
        for method in ('post', 'put', 'patch', 'delete'):
            response = getattr(self.client, method)(choose, {'option': self.option.pk})
            self.assertEqual(response.status_code, 403)
        for url in (reverse('projects:detail', args=[self.other_project.pk]),
                    reverse('projects:selection_detail', args=[self.other_project.pk, self.selection.pk]),
                    reverse('projects:company_list'), reverse('projects:selection_template_home'),
                    reverse('users:security'), reverse('projects:notification_list'),
                    reverse('projects:financial_summary', args=[self.project.pk])):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.selection.refresh_from_db()
        self.assertEqual(self.selection.status, 'open')
        self.assertEqual(self.client.post(reverse('projects:message_create', args=[self.project.pk]),
            {'subject': 'Must not send', 'body': 'No'}).status_code, 403)
        self.assertFalse(ConversationThread.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_preview_does_not_mark_client_views_or_signature_reviews(self):
        signature = DocumentSignatureRequest.objects.create(version=self.version, signer=self.customer,
            requested_by=self.staff, document_sha256='a' * 64, consent_text='Test consent')
        self.enter()
        for url in (self.detail, reverse('projects:document_signature_review',
                    args=[self.project.pk, self.document.pk, signature.pk]),
                    reverse('projects:notification_settings', args=[self.project.pk])):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertFalse(ActivityEvent.objects.filter(event_type='client_viewed').exists())
        self.assertFalse(DocumentSignatureEvent.objects.exists())
        self.assertFalse(NotificationPreference.objects.exists())
        self.assertEqual(self.client.post(reverse('projects:document_signature_review',
            args=[self.project.pk, self.document.pk, signature.pk]), {'action': 'sign', 'typed_name': 'No'}).status_code, 403)
        signature.refresh_from_db()
        self.assertEqual(signature.status, 'pending')

    def test_no_client_uses_unsaved_generic_preview_without_creating_accounts(self):
        self.client_membership.delete()
        users_before = get_user_model().objects.count()
        self.enter()
        for name, args in [('detail', [self.project.pk]), ('action_center', [self.project.pk]),
                           ('message_list', [self.project.pk]), ('document_list', [self.project.pk]),
                           ('selection_list', [self.project.pk]), ('daily_log_list', [self.project.pk]),
                           ('estimate_list', [self.project.pk]), ('change_order_list', [self.project.pk]),
                           ('notification_settings', [self.project.pk])]:
            response = self.client.get(reverse('projects:' + name, args=args))
            self.assertEqual(response.status_code, 200, name)
            self.assertNotContains(response, 'PRIVATE DRAFT')
            self.assertNotContains(response, 'PRIVATE DOCUMENT')
        self.assertEqual(self.client.get(reverse('billing:invoice_list', args=[self.project.pk])).status_code, 200)
        self.assertEqual(get_user_model().objects.count(), users_before)
        self.assertFalse(self.project.project_memberships.exists())

    def test_revalidation_revokes_preview_when_staff_access_is_removed(self):
        self.enter()
        self.staff_membership.is_active = False
        self.staff_membership.save()
        self.assertEqual(self.client.get(self.detail).status_code, 403)
        self.assertEqual(self.client.post(self.stop).status_code, 302)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_privileged_client_identity_is_never_used_for_preview(self):
        OrganizationMembership.objects.filter(user=self.customer).update(role='admin')
        self.customer.is_superuser = True
        self.customer.save()
        self.enter()
        response = self.client.get(self.detail)
        self.assertNotContains(response, 'CONFIDENTIAL COMPANY NOTES')
        self.assertFalse(response.wsgi_request.user.is_superuser)
        self.assertNotEqual(response.wsgi_request.user.pk, self.customer.pk)
        self.assertNotContains(self.client.get(reverse('projects:selection_list', args=[self.project.pk])), 'PRIVATE DRAFT')

    def test_preview_rechecks_project_assignment_and_never_runs_mutation_as_staff(self):
        self.enter()
        self.project.internal_access.update(is_active=False)
        self.assertEqual(self.client.get(self.detail).status_code, 403)
        self.assertEqual(self.client.post(reverse('projects:edit', args=[self.project.pk]),
            {'name': 'Must not change'}).status_code, 403)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Client home')

    def test_switch_requires_post_and_csrf_and_keeps_client_session_independent(self):
        self.assertEqual(self.client.get(self.stop).status_code, 405)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.staff)
        self.assertEqual(secure.post(self.start, {'project': self.project.pk}).status_code, 403)
        self.enter()
        session = secure.session
        session[SESSION_KEY] = self.project.pk
        session.save()
        self.assertEqual(secure.post(self.stop).status_code, 403)
        customer_browser = Client()
        customer_browser.force_login(self.customer)
        self.assertNotContains(customer_browser.get(self.detail), 'Back to staff view')
        self.assertEqual(customer_browser.session['_auth_user_id'], str(self.customer.pk))

    def test_preview_keeps_staff_mfa_enforcement(self):
        self.enter()
        with patch('users.mfa.enrollment_required', return_value=True) as required:
            response = self.client.get(self.detail)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('users:mfa_setup'), response.url)
        self.assertEqual(required.call_args.args[0].pk, self.staff.pk)
        self.assertEqual(self.client.session['_auth_user_id'], str(self.staff.pk))
