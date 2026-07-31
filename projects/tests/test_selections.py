import shutil
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from projects.models import (
    ActivityEvent,
    FinishSelection,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
    SelectionCustomRequest,
    SelectionOption,
    SelectionPackage,
)
from projects.storage import private_document_storage
from projects.tests import grant_internal_access


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class FinishSelectionTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_media_directory = Path(tempfile.mkdtemp(prefix='selection-options-'))
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
        cls.second_client = get_user_model().objects.create_user(
            'second-client@example.com', 'password'
        )
        cls.subcontractor = get_user_model().objects.create_user(
            'sub@example.com', 'password'
        )
        for user, role in (
            (cls.admin_user, OrganizationMembership.Role.ADMIN),
            (cls.staff_user, OrganizationMembership.Role.STAFF),
            (cls.accountant, OrganizationMembership.Role.ACCOUNTANT),
            (cls.client_user, OrganizationMembership.Role.CLIENT),
            (cls.second_client, OrganizationMembership.Role.CLIENT),
            (cls.subcontractor, OrganizationMembership.Role.SUBCONTRACTOR),
        ):
            OrganizationMembership.objects.create(
                organization=cls.organization, user=user, role=role
            )
        for user, role in (
            (cls.client_user, OrganizationMembership.Role.CLIENT),
            (cls.second_client, OrganizationMembership.Role.CLIENT),
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

    def selection_form_data(self, title='Kitchen countertops'):
        return {
            'title': title,
            'description': 'Choose the kitchen countertop finish.',
            'location': 'Kitchen',
            'allowance_amount': '1000.00',
            'due_date': '2026-08-15',
        }

    def create_selection(
        self,
        *,
        project=None,
        status=FinishSelection.Status.DRAFT,
        with_option=True,
        number=1,
    ):
        project = project or self.project
        effective_status = (
            FinishSelection.Status.OPEN
            if status == FinishSelection.Status.SELECTED
            else status
        )
        data = {
            'project': project,
            'number': number,
            'title': 'Kitchen countertops',
            'description': 'Choose the kitchen countertop finish.',
            'location': 'Kitchen',
            'allowance_amount': Decimal('1000.00'),
            'due_date': date(2026, 8, 15),
            'status': effective_status,
            'created_by': self.admin_user,
        }
        if effective_status != FinishSelection.Status.DRAFT:
            data.update(
                {'opened_by': self.admin_user, 'opened_at': timezone.now()}
            )
        selection = FinishSelection.objects.create(**data)
        option = None
        if with_option:
            option = SelectionOption.objects.create(
                selection=selection,
                name='Quartz - Calacatta',
                description='White quartz with soft gray veining.',
                price=Decimal('1200.00'),
                cost=Decimal('700.00'),
                is_recommended=True,
            )
        if status == FinishSelection.Status.SELECTED:
            selection.status = FinishSelection.Status.SELECTED
            selection.chosen_option = option
            selection.selected_by = self.client_user
            selection.selected_at = timezone.now()
            selection.save(
                update_fields=(
                    'status',
                    'chosen_option',
                    'selected_by',
                    'selected_at',
                    'updated_at',
                )
            )
        return selection, option

    def test_admin_creates_sequential_selection_drafts(self):
        self.client.force_login(self.admin_user)
        first_response = self.client.post(
            reverse('projects:selection_create', args=(self.project.pk,)),
            self.selection_form_data(),
        )
        second_response = self.client.post(
            reverse('projects:selection_create', args=(self.project.pk,)),
            self.selection_form_data('Primary bathroom tile'),
        )

        first, second = FinishSelection.objects.order_by('number')
        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(first.display_number, 'SEL-001')
        self.assertEqual(second.display_number, 'SEL-002')
        self.assertEqual(first.status, FinishSelection.Status.DRAFT)
        self.assertEqual(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.SELECTION_CREATED
            ).count(),
            2,
        )

    def test_draft_selection_is_hidden_from_clients(self):
        selection, _ = self.create_selection()
        self.client.force_login(self.client_user)
        list_response = self.client.get(
            reverse('projects:selection_list', args=(self.project.pk,))
        )
        detail_response = self.client.get(
            reverse(
                'projects:selection_detail',
                args=(self.project.pk, selection.pk),
            )
        )

        self.assertNotContains(list_response, selection.title)
        self.assertEqual(detail_response.status_code, 404)

    def test_staff_adds_option_with_allowance_math_and_audit(self):
        selection, _ = self.create_selection(with_option=False)
        self.client.force_login(self.staff_user)
        response = self.client.post(
            reverse(
                'projects:selection_option_create',
                args=(self.project.pk, selection.pk),
            ),
            {
                'name': 'Quartz - Calacatta',
                'description': 'White quartz.',
                'price': '1200.00',
                'cost': '700.00',
                'is_recommended': 'on',
                'sort_order': '1',
            },
        )

        option = selection.options.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(option.allowance_variance, Decimal('200.00'))
        self.assertEqual(option.margin, Decimal('500.00'))
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.SELECTION_OPTION_ADDED
            ).exists()
        )

    def test_publish_requires_an_option_and_an_active_client(self):
        no_option, _ = self.create_selection(with_option=False)
        no_client, _ = self.create_selection(project=self.other_project)
        self.client.force_login(self.admin_user)

        option_response = self.client.post(
            reverse(
                'projects:selection_publish',
                args=(self.project.pk, no_option.pk),
            ),
            follow=True,
        )
        client_response = self.client.post(
            reverse(
                'projects:selection_publish',
                args=(self.other_project.pk, no_client.pk),
            ),
            follow=True,
        )
        no_option.refresh_from_db()
        no_client.refresh_from_db()

        self.assertContains(option_response, 'Add at least one option')
        self.assertContains(client_response, 'Assign an active client')
        self.assertEqual(no_option.status, FinishSelection.Status.DRAFT)
        self.assertEqual(no_client.status, FinishSelection.Status.DRAFT)
        self.assertEqual(len(mail.outbox), 0)

    def test_publish_notifies_clients_and_locks_selection_and_options(self):
        selection, option = self.create_selection()
        self.client.force_login(self.staff_user)
        response = self.client.post(
            reverse(
                'projects:selection_publish',
                args=(self.project.pk, selection.pk),
            )
        )
        selection.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(selection.status, FinishSelection.Status.OPEN)
        self.assertEqual(selection.opened_by, self.staff_user)
        self.assertEqual(
            mail.outbox[0].to,
            ['client@example.com', 'second-client@example.com'],
        )
        self.assertEqual(
            self.client.get(
                reverse(
                    'projects:selection_edit',
                    args=(self.project.pk, selection.pk),
                )
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse(
                    'projects:selection_option_edit',
                    args=(self.project.pk, selection.pk, option.pk),
                )
            ).status_code,
            403,
        )

    def test_client_sees_allowance_and_price_but_not_cost_or_margin(self):
        selection, _ = self.create_selection(status=FinishSelection.Status.OPEN)
        self.client.force_login(self.client_user)
        response = self.client.get(
            reverse(
                'projects:selection_detail',
                args=(self.project.pk, selection.pk),
            )
        )

        self.assertContains(response, '$1000.00')
        self.assertContains(response, '$1200.00')
        self.assertContains(response, '+$200.00 over allowance')
        self.assertNotContains(response, '$700.00')
        self.assertNotContains(response, 'Estimated margin')
        self.assertContains(response, 'still requires a separate change order')

    def test_staff_sees_internal_option_cost_and_margin(self):
        selection, _ = self.create_selection(status=FinishSelection.Status.OPEN)
        self.client.force_login(self.staff_user)
        response = self.client.get(
            reverse(
                'projects:selection_detail',
                args=(self.project.pk, selection.pk),
            )
        )

        self.assertContains(response, 'Estimated cost: $700.00')
        self.assertContains(response, 'Estimated margin: $500.00')

    def test_first_authenticated_client_choice_is_recorded_and_locked(self):
        selection, option = self.create_selection(status=FinishSelection.Status.OPEN)
        self.client.force_login(self.client_user)
        response = self.client.post(
            reverse(
                'projects:selection_choose',
                args=(self.project.pk, selection.pk),
            ),
            {'option': option.pk, 'comment': 'This is our preferred finish.'},
        )
        selection.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(selection.status, FinishSelection.Status.SELECTED)
        self.assertEqual(selection.chosen_option, option)
        self.assertEqual(selection.selected_by, self.client_user)
        self.assertEqual(
            mail.outbox[0].to, ['admin@example.com', 'staff@example.com']
        )
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.SELECTION_CHOSEN,
                actor=self.client_user,
            ).exists()
        )

        self.client.force_login(self.second_client)
        self.client.post(
            reverse(
                'projects:selection_choose',
                args=(self.project.pk, selection.pk),
            ),
            {'option': option.pk},
        )
        selection.refresh_from_db()
        self.assertEqual(selection.selected_by, self.client_user)
        self.assertEqual(len(mail.outbox), 1)

    def test_manager_reopens_completed_selection_for_a_new_client_choice(self):
        selection, option = self.create_selection(
            status=FinishSelection.Status.SELECTED
        )
        selection.client_comment = 'Original choice.'
        selection.save(update_fields=('client_comment', 'updated_at'))
        self.client.force_login(self.staff_user)

        response = self.client.post(
            reverse(
                'projects:selection_reopen',
                args=(self.project.pk, selection.pk),
            )
        )

        selection.refresh_from_db()
        self.assertRedirects(
            response,
            reverse(
                'projects:selection_detail',
                args=(self.project.pk, selection.pk),
            ),
        )
        self.assertEqual(selection.status, FinishSelection.Status.OPEN)
        self.assertIsNone(selection.chosen_option)
        self.assertIsNone(selection.selected_by)
        self.assertIsNone(selection.selected_at)
        self.assertEqual(selection.client_comment, '')
        self.assertEqual(
            mail.outbox[0].to,
            ['client@example.com', 'second-client@example.com'],
        )
        event = ActivityEvent.objects.get(
            project=self.project,
            event_type=ActivityEvent.Type.SELECTION_REOPENED,
        )
        self.assertEqual(event.metadata['previous_option_id'], option.pk)

    def test_selected_overage_links_to_prefilled_change_order(self):
        selection, _ = self.create_selection(
            status=FinishSelection.Status.SELECTED
        )
        self.client.force_login(self.staff_user)

        detail_response = self.client.get(
            reverse(
                'projects:selection_detail',
                args=(self.project.pk, selection.pk),
            )
        )
        change_order_response = self.client.get(
            reverse('projects:change_order_create', args=(self.project.pk,)),
            {'selection': selection.pk},
        )

        self.assertContains(detail_response, 'Create change order')
        form = change_order_response.context['form']
        self.assertEqual(
            form.initial['title'],
            'Kitchen countertops allowance overage',
        )
        self.assertEqual(form.initial['price_delta'], Decimal('200.00'))

    def test_option_from_another_selection_cannot_be_chosen(self):
        selection, _ = self.create_selection(status=FinishSelection.Status.OPEN)
        other_selection, other_option = self.create_selection(
            project=self.other_project,
            status=FinishSelection.Status.OPEN,
        )
        self.client.force_login(self.client_user)
        response = self.client.post(
            reverse(
                'projects:selection_choose',
                args=(self.project.pk, selection.pk),
            ),
            {'option': other_option.pk},
            follow=True,
        )
        selection.refresh_from_db()

        self.assertContains(response, 'Choose an option before submitting')
        self.assertEqual(selection.status, FinishSelection.Status.OPEN)
        self.assertNotEqual(selection.pk, other_selection.pk)

    def test_accountant_and_subcontractor_cannot_access_selections(self):
        url = reverse('projects:selection_list', args=(self.project.pk,))
        for user in (self.accountant, self.subcontractor):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_voided_selection_notifies_clients_and_cannot_be_chosen(self):
        selection, option = self.create_selection(status=FinishSelection.Status.OPEN)
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse(
                'projects:selection_void',
                args=(self.project.pk, selection.pk),
            )
        )
        selection.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(selection.status, FinishSelection.Status.VOIDED)
        self.assertEqual(selection.voided_by, self.admin_user)
        self.assertEqual(
            mail.outbox[0].to,
            ['client@example.com', 'second-client@example.com'],
        )

        self.client.force_login(self.client_user)
        self.client.post(
            reverse(
                'projects:selection_choose',
                args=(self.project.pk, selection.pk),
            ),
            {'option': option.pk},
        )
        selection.refresh_from_db()
        self.assertEqual(selection.status, FinishSelection.Status.VOIDED)
        self.assertEqual(len(mail.outbox), 1)

    def test_new_package_title_creates_and_groups_selections(self):
        self.client.force_login(self.admin_user)
        data = self.selection_form_data('Kitchen faucet')
        data['new_package_title'] = 'Kitchen'
        response = self.client.post(
            reverse('projects:selection_create', args=(self.project.pk,)), data
        )
        self.assertEqual(response.status_code, 302)
        selection = FinishSelection.objects.get(title='Kitchen faucet')
        self.assertIsNotNone(selection.package_id)
        self.assertEqual(selection.package.title, 'Kitchen')
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.SELECTION_PACKAGE_CREATED
            ).exists()
        )

        second_data = self.selection_form_data('Kitchen sink')
        second_data['new_package_title'] = 'kitchen'
        self.client.post(
            reverse('projects:selection_create', args=(self.project.pk,)), second_data
        )
        second_selection = FinishSelection.objects.get(title='Kitchen sink')
        self.assertEqual(second_selection.package_id, selection.package_id)
        self.assertEqual(SelectionPackage.objects.count(), 1)

        list_response = self.client.get(
            reverse('projects:selection_list', args=(self.project.pk,))
        )
        package_groups = dict(list_response.context['package_groups'])
        self.assertEqual(len(package_groups[selection.package]), 2)

        package_detail = self.client.get(
            reverse(
                'projects:selection_package_detail',
                args=(self.project.pk, selection.package.pk),
            )
        )
        self.assertEqual(len(package_detail.context['choices']), 2)

    def test_package_and_new_package_title_conflict_rejected(self):
        package = SelectionPackage.objects.create(
            project=self.project, title='Bathroom', created_by=self.admin_user
        )
        self.client.force_login(self.admin_user)
        data = self.selection_form_data('Bathroom vanity')
        data['package'] = package.pk
        data['new_package_title'] = 'Bathroom 2'
        response = self.client.post(
            reverse('projects:selection_create', args=(self.project.pk,)), data
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            FinishSelection.objects.filter(title='Bathroom vanity').exists()
        )

    def create_option_with_metadata(self, selection):
        create_url = reverse(
            'projects:selection_option_create', args=(self.project.pk, selection.pk)
        )
        self.client.post(
            create_url,
            {
                'name': 'Quartz - Calacatta',
                'description': 'White quartz.',
                'price': '1200.00',
                'cost': '700.00',
                'is_recommended': False,
                'sort_order': '0',
                'vendor_name': 'Stone Supply Co',
                'product_url': 'https://example.com/quartz',
                'specification': 'Polished, 3cm',
                'lead_time': '6-8 weeks',
                'image': SimpleUploadedFile(
                    'swatch.jpg', b'fake-jpg-bytes', content_type='image/jpeg'
                ),
                'attachment': SimpleUploadedFile(
                    'spec.pdf', b'%PDF-1.4 spec', content_type='application/pdf'
                ),
            },
        )
        return SelectionOption.objects.get(selection=selection)

    def test_option_metadata_round_trips_and_rejects_bad_image_type(self):
        selection, _ = self.create_selection(with_option=False)
        self.client.force_login(self.admin_user)
        create_url = reverse(
            'projects:selection_option_create', args=(self.project.pk, selection.pk)
        )
        bad_response = self.client.post(
            create_url,
            {
                'name': 'Quartz - Calacatta',
                'description': 'White quartz.',
                'price': '1200.00',
                'cost': '700.00',
                'is_recommended': False,
                'sort_order': '0',
                'image': SimpleUploadedFile(
                    'swatch.txt', b'not an image', content_type='text/plain'
                ),
            },
        )
        self.assertContains(bad_response, 'Unsupported image type')
        self.assertFalse(SelectionOption.objects.filter(selection=selection).exists())

        option = self.create_option_with_metadata(selection)
        self.assertEqual(option.vendor_name, 'Stone Supply Co')
        self.assertEqual(option.lead_time, '6-8 weeks')
        self.assertTrue(option.image.name)
        self.assertTrue(option.attachment.name)

    def test_option_image_download_requires_visibility(self):
        selection, _ = self.create_selection(with_option=False)
        self.client.force_login(self.admin_user)
        option = self.create_option_with_metadata(selection)
        image_url = reverse(
            'projects:selection_option_image',
            args=(self.project.pk, selection.pk, option.pk),
        )

        self.client.force_login(self.client_user)
        self.assertEqual(self.client.get(image_url).status_code, 404)

        self.client.force_login(self.admin_user)
        self.client.post(
            reverse('projects:selection_publish', args=(self.project.pk, selection.pk))
        )
        self.client.force_login(self.client_user)
        self.assertEqual(self.client.get(image_url).status_code, 200)

    def test_client_custom_request_routes_to_prefilled_change_order(self):
        selection, _ = self.create_selection(status=FinishSelection.Status.OPEN)
        self.client.force_login(self.client_user)
        response = self.client.post(
            reverse(
                'projects:selection_custom_request_create',
                args=(self.project.pk, selection.pk),
            ),
            {
                'description': 'Can we get a waterfall edge instead?',
                'target_price': '1500.00',
            },
        )
        self.assertEqual(response.status_code, 302)
        custom_request = SelectionCustomRequest.objects.get()
        self.assertEqual(custom_request.requested_by, self.client_user)
        self.assertEqual(
            custom_request.status, SelectionCustomRequest.Status.PENDING
        )
        self.assertEqual(
            mail.outbox[0].to, ['admin@example.com', 'staff@example.com']
        )

        self.client.force_login(self.staff_user)
        review_response = self.client.post(
            reverse(
                'projects:selection_custom_request_review',
                args=(self.project.pk, selection.pk, custom_request.pk),
            )
        )
        custom_request.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            custom_request.status, SelectionCustomRequest.Status.REVIEWED
        )
        self.assertEqual(custom_request.reviewed_by, self.staff_user)
        self.assertIn(
            f'/change-orders/new/?custom_request={custom_request.pk}',
            review_response['Location'],
        )

        create_response = self.client.get(review_response['Location'])
        form = create_response.context['form']
        self.assertEqual(
            form.initial['description'], 'Can we get a waterfall edge instead?'
        )
        self.assertEqual(form.initial['price_delta'], Decimal('1500.00'))

        second_attempt = self.client.post(
            reverse(
                'projects:selection_custom_request_review',
                args=(self.project.pk, selection.pk, custom_request.pk),
            )
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(second_attempt.status_code, 302)

    def test_credit_disposition_flagged_set_by_staff_only_and_reset_on_reopen(self):
        selection, _ = self.create_selection(with_option=False)
        credit_option = SelectionOption.objects.create(
            selection=selection,
            name='Laminate',
            price=Decimal('700.00'),
            cost=Decimal('300.00'),
        )
        selection.status = FinishSelection.Status.OPEN
        selection.opened_by = self.admin_user
        selection.opened_at = timezone.now()
        selection.save(update_fields=['status', 'opened_by', 'opened_at'])

        self.client.force_login(self.client_user)
        self.client.post(
            reverse('projects:selection_choose', args=(self.project.pk, selection.pk)),
            {'option': credit_option.pk},
        )
        selection.refresh_from_db()
        self.assertTrue(selection.has_credit)
        self.assertEqual(
            selection.credit_disposition,
            FinishSelection.CreditDisposition.UNDETERMINED,
        )

        disposition_url = reverse(
            'projects:selection_credit_disposition', args=(self.project.pk, selection.pk)
        )
        client_attempt = self.client.post(
            disposition_url, {'credit_disposition': 'retain_as_margin'}
        )
        self.assertEqual(client_attempt.status_code, 403)

        self.client.force_login(self.admin_user)
        staff_response = self.client.post(
            disposition_url, {'credit_disposition': 'retain_as_margin'}
        )
        self.assertEqual(staff_response.status_code, 302)
        selection.refresh_from_db()
        self.assertEqual(
            selection.credit_disposition,
            FinishSelection.CreditDisposition.RETAIN_AS_MARGIN,
        )
        self.assertEqual(selection.credit_disposition_set_by, self.admin_user)
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.SELECTION_CREDIT_DISPOSITION_SET
            ).exists()
        )

        second_attempt = self.client.post(
            disposition_url, {'credit_disposition': 'return_at_closing'}
        )
        self.assertEqual(second_attempt.status_code, 302)
        selection.refresh_from_db()
        self.assertEqual(
            selection.credit_disposition,
            FinishSelection.CreditDisposition.RETAIN_AS_MARGIN,
        )

        self.client.post(
            reverse('projects:selection_reopen', args=(self.project.pk, selection.pk))
        )
        selection.refresh_from_db()
        self.assertEqual(
            selection.credit_disposition,
            FinishSelection.CreditDisposition.UNDETERMINED,
        )
        self.assertIsNone(selection.credit_disposition_set_by)

    def test_credit_disposition_rejected_when_no_credit(self):
        selection, _ = self.create_selection(status=FinishSelection.Status.SELECTED)
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse(
                'projects:selection_credit_disposition',
                args=(self.project.pk, selection.pk),
            ),
            {'credit_disposition': 'retain_as_margin'},
        )
        self.assertEqual(response.status_code, 403)

    def test_manual_reminder_sends_email_and_records_activity(self):
        selection, _ = self.create_selection(status=FinishSelection.Status.OPEN)
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse('projects:selection_remind', args=(self.project.pk, selection.pk))
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            mail.outbox[0].to, ['client@example.com', 'second-client@example.com']
        )
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.SELECTION_REMINDER_SENT,
                actor=self.admin_user,
            ).exists()
        )

    def test_overdue_reminder_command_only_emails_overdue_open_selections(self):
        overdue_selection, _ = self.create_selection(
            status=FinishSelection.Status.OPEN, number=1
        )
        overdue_selection.due_date = date(2020, 1, 1)
        overdue_selection.save(update_fields=['due_date'])
        upcoming_selection, _ = self.create_selection(
            status=FinishSelection.Status.OPEN, number=2
        )
        upcoming_selection.due_date = date(2999, 1, 1)
        upcoming_selection.save(update_fields=['due_date'])

        call_command('send_overdue_selection_reminders')

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(overdue_selection.display_number, mail.outbox[0].body)
        reminder_events = ActivityEvent.objects.filter(
            event_type=ActivityEvent.Type.SELECTION_REMINDER_SENT
        )
        self.assertEqual(reminder_events.count(), 1)
        event = reminder_events.get()
        self.assertIsNone(event.actor)
        self.assertEqual(event.metadata['selection_id'], overdue_selection.pk)
