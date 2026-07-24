from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
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
    SelectionOption,
)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class FinishSelectionTests(TestCase):
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
    ):
        project = project or self.project
        effective_status = (
            FinishSelection.Status.OPEN
            if status == FinishSelection.Status.SELECTED
            else status
        )
        data = {
            'project': project,
            'number': 1,
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
