from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from projects.models import (
    ActivityEvent,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
    ScheduleMilestone,
)
from projects.schedule_calendar import shift_month
from projects.tests import grant_internal_access


class ProjectScheduleTests(TestCase):
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

    def form_data(self, **overrides):
        data = {
            'title': 'Framing',
            'description': 'Exterior walls and roof framing.',
            'start_date': '2026-08-03',
            'end_date': '2026-08-14',
            'status': ScheduleMilestone.Status.PLANNED,
            'internal_notes': 'Coordinate truss delivery with field supervisor.',
            'sort_order': '0',
        }
        data.update(overrides)
        return data

    def create_milestone(self, *, project=None, client_visible=False):
        return ScheduleMilestone.objects.create(
            project=project or self.project,
            title='Framing',
            description='Exterior walls and roof framing.',
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 14),
            client_visible=client_visible,
            internal_notes='Coordinate truss delivery with field supervisor.',
            created_by=self.admin_user,
        )

    def test_staff_creates_internal_milestone_without_client_email(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(
            reverse('projects:schedule_milestone_create', args=(self.project.pk,)),
            self.form_data(),
        )

        milestone = ScheduleMilestone.objects.get()
        self.assertRedirects(
            response, reverse('projects:schedule', args=(self.project.pk,))
        )
        self.assertEqual(milestone.created_by, self.staff_user)
        self.assertFalse(milestone.client_visible)
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.SCHEDULE_MILESTONE_CREATED,
                actor=self.staff_user,
            ).exists()
        )

    def test_schedule_is_not_available_in_client_portal(self):
        self.create_milestone()
        self.client.force_login(self.client_user)
        response = self.client.get(
            reverse('projects:schedule', args=(self.project.pk,))
        )
        self.assertEqual(response.status_code, 403)

    def test_schedule_calendar_renders_requested_month_and_full_date_range(self):
        milestone = self.create_milestone()
        self.client.force_login(self.staff_user)
        response = self.client.get(
            reverse('projects:schedule', args=(self.project.pk,)),
            {'month': '2026-08'},
        )

        event_dates = [
            day['date']
            for week in response.context['calendar_weeks']
            for day in week
            if any(
                event['milestone'] == milestone for event in day['events']
            )
        ]
        expected_dates = [
            milestone.start_date + timedelta(days=offset)
            for offset in range(12)
        ]

        self.assertEqual(response.context['calendar_month'], date(2026, 8, 1))
        self.assertEqual(response.context['previous_month'], date(2026, 7, 1))
        self.assertEqual(response.context['next_month'], date(2026, 9, 1))
        self.assertEqual(event_dates, expected_dates)
        self.assertContains(response, 'August 2026')
        self.assertContains(response, '?month=2026-07')
        self.assertContains(response, '?month=2026-09')
        self.assertContains(response, 'Starts')
        self.assertContains(response, 'Ends')

    @patch('projects.views.timezone.localdate', return_value=date(2026, 7, 1))
    def test_schedule_calendar_defaults_to_next_relevant_month(self, _localdate):
        self.create_milestone()
        self.client.force_login(self.staff_user)

        response = self.client.get(
            reverse('projects:schedule', args=(self.project.pk,)),
        )

        self.assertEqual(response.context['calendar_month'], date(2026, 8, 1))

    def test_invalid_calendar_month_is_ignored(self):
        self.create_milestone()
        self.client.force_login(self.staff_user)

        with patch(
            'projects.views.timezone.localdate',
            return_value=date(2026, 8, 5),
        ):
            response = self.client.get(
                reverse('projects:schedule', args=(self.project.pk,)),
                {'month': '2026-99'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['calendar_month'], date(2026, 8, 1))

    def test_calendar_navigation_is_safe_at_date_boundaries(self):
        self.assertEqual(shift_month(date.min, -1), date.min)
        self.assertEqual(
            shift_month(date.max.replace(day=1), 1),
            date.max.replace(day=1),
        )

    def test_client_cannot_open_internal_schedule(self):
        self.create_milestone()
        self.client.force_login(self.client_user)
        response = self.client.get(
            reverse('projects:schedule', args=(self.project.pk,))
        )
        self.assertEqual(response.status_code, 403)

    def test_staff_sees_internal_notes_and_edit_control(self):
        milestone = self.create_milestone()
        self.client.force_login(self.staff_user)
        response = self.client.get(
            reverse('projects:schedule', args=(self.project.pk,))
        )

        self.assertContains(response, milestone.internal_notes)
        self.assertContains(response, 'Edit milestone')

    def test_end_date_before_start_date_is_rejected(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse('projects:schedule_milestone_create', args=(self.project.pk,)),
            self.form_data(start_date='2026-08-14', end_date='2026-08-03'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'End date cannot be before the start date')
        self.assertFalse(ScheduleMilestone.objects.exists())

    def test_update_changes_status_and_is_audited_without_client_notification(self):
        milestone = self.create_milestone()
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse(
                'projects:schedule_milestone_edit',
                args=(self.project.pk, milestone.pk),
            ),
            self.form_data(status=ScheduleMilestone.Status.IN_PROGRESS),
        )
        milestone.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(milestone.status, ScheduleMilestone.Status.IN_PROGRESS)
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.SCHEDULE_MILESTONE_UPDATED
            ).exists()
        )

    def test_client_accountant_and_subcontractor_cannot_manage_schedule(self):
        milestone = self.create_milestone()
        create_url = reverse(
            'projects:schedule_milestone_create', args=(self.project.pk,)
        )
        edit_url = reverse(
            'projects:schedule_milestone_edit',
            args=(self.project.pk, milestone.pk),
        )
        for user in (self.client_user, self.accountant, self.subcontractor):
            self.client.force_login(user)
            self.assertEqual(self.client.get(create_url).status_code, 403)
            self.assertEqual(self.client.get(edit_url).status_code, 403)

    def test_client_accountant_and_subcontractor_cannot_view_schedule(self):
        url = reverse('projects:schedule', args=(self.project.pk,))
        for user in (self.client_user, self.accountant, self.subcontractor):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_milestone_from_another_project_cannot_be_edited(self):
        milestone = self.create_milestone(project=self.other_project)
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse(
                'projects:schedule_milestone_edit',
                args=(self.project.pk, milestone.pk),
            )
        )
        self.assertEqual(response.status_code, 404)
