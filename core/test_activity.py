import csv
import io
from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from projects.models import (
    ActivityEvent,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
)


class ProjectActivityListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(
            name='Example Builders',
            slug='example-builders',
        )
        cls.oak_project = Project.objects.create(
            organization=cls.organization,
            name='Oak Street',
            code='OAK-01',
        )
        cls.pine_project = Project.objects.create(
            organization=cls.organization,
            name='Pine Street',
            code='PINE-02',
        )
        cls.other_organization = Organization.objects.create(
            name='Other Builders',
            slug='other-builders',
        )
        cls.hidden_project = Project.objects.create(
            organization=cls.other_organization,
            name='Hidden Project',
        )

        user_model = get_user_model()
        cls.staff_user = user_model.objects.create_user(
            'staff@example.com',
            'password',
        )
        cls.accountant = user_model.objects.create_user(
            'accountant@example.com',
            'password',
        )
        cls.client_user = user_model.objects.create_user(
            'client@example.com',
            'password',
        )
        cls.subcontractor = user_model.objects.create_user(
            'sub@example.com',
            'password',
        )
        for user, role in (
            (cls.staff_user, OrganizationMembership.Role.STAFF),
            (cls.accountant, OrganizationMembership.Role.ACCOUNTANT),
            (cls.client_user, OrganizationMembership.Role.CLIENT),
            (cls.subcontractor, OrganizationMembership.Role.SUBCONTRACTOR),
        ):
            OrganizationMembership.objects.create(
                organization=cls.organization,
                user=user,
                role=role,
            )
        for user, role in (
            (cls.client_user, OrganizationMembership.Role.CLIENT),
            (cls.subcontractor, OrganizationMembership.Role.SUBCONTRACTOR),
        ):
            ProjectMembership.objects.create(
                project=cls.oak_project,
                user=user,
                role=role,
            )

        cls.oak_event = ActivityEvent.objects.create(
            organization=cls.organization,
            project=cls.oak_project,
            actor=cls.staff_user,
            event_type=ActivityEvent.Type.DOCUMENT_CREATED,
            summary='Foundation plan was added.',
            metadata={'private_cost': '98765.43'},
        )
        cls.pine_event = ActivityEvent.objects.create(
            organization=cls.organization,
            project=cls.pine_project,
            actor=cls.staff_user,
            event_type=ActivityEvent.Type.PROJECT_UPDATED,
            summary='Pine Street schedule was updated.',
        )
        ActivityEvent.objects.create(
            organization=cls.other_organization,
            project=cls.hidden_project,
            actor=cls.staff_user,
            event_type=ActivityEvent.Type.PROJECT_UPDATED,
            summary='Hidden activity must not appear.',
        )
        ActivityEvent.objects.create(
            organization=cls.organization,
            actor=cls.staff_user,
            event_type=ActivityEvent.Type.TEAM_ROLE_CHANGED,
            summary='Company-only activity must not appear.',
        )

    def test_staff_sees_only_authorized_project_activity_without_metadata(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse('core:activity_list'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['activity_events']),
            [self.pine_event, self.oak_event],
        )
        self.assertContains(response, 'Project activity')
        self.assertContains(response, 'Pine Street schedule was updated.')
        self.assertContains(response, 'Foundation plan was added.')
        self.assertNotContains(response, 'Hidden activity must not appear.')
        self.assertNotContains(response, 'Company-only activity must not appear.')
        self.assertNotContains(response, '98765.43')

    def test_activity_page_requires_management_access(self):
        url = reverse('core:activity_list')
        anonymous_response = self.client.get(url)
        self.assertEqual(anonymous_response.status_code, 302)
        self.assertIn(reverse('login'), anonymous_response.url)

        for user in (self.accountant, self.client_user, self.subcontractor):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_activity_filters_by_project_type_and_search(self):
        self.client.force_login(self.staff_user)

        project_response = self.client.get(
            reverse('core:activity_list'),
            {'project': self.oak_project.pk},
        )
        self.assertEqual(
            list(project_response.context['activity_events']),
            [self.oak_event],
        )
        self.assertEqual(
            project_response.context['activity_project'],
            self.oak_project.pk,
        )

        type_response = self.client.get(
            reverse('core:activity_list'),
            {'type': ActivityEvent.Type.PROJECT_UPDATED},
        )
        self.assertEqual(
            list(type_response.context['activity_events']),
            [self.pine_event],
        )

        search_response = self.client.get(
            reverse('core:activity_list'),
            {'q': 'foundation'},
        )
        self.assertEqual(
            list(search_response.context['activity_events']),
            [self.oak_event],
        )
        self.assertNotContains(search_response, 'No activity matches')

    def test_hidden_or_invalid_filter_values_are_ignored(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(
            reverse('core:activity_list'),
            {
                'project': self.hidden_project.pk,
                'type': ActivityEvent.Type.TEAM_ROLE_CHANGED,
            },
        )

        self.assertIsNone(response.context['activity_project'])
        self.assertEqual(response.context['activity_type'], '')
        self.assertEqual(response.context['page_obj'].paginator.count, 2)
        self.assertNotContains(response, 'Hidden activity must not appear.')

        oversized_response = self.client.get(
            reverse('core:activity_list'),
            {'project': '9' * 5000},
        )
        self.assertEqual(oversized_response.status_code, 200)
        self.assertIsNone(oversized_response.context['activity_project'])

    def test_activity_filters_by_valid_date_range_and_ignores_invalid_dates(self):
        ActivityEvent.objects.filter(pk=self.oak_event.pk).update(
            created_at=timezone.make_aware(datetime(2026, 7, 1, 12, 0))
        )
        ActivityEvent.objects.filter(pk=self.pine_event.pk).update(
            created_at=timezone.make_aware(datetime(2026, 7, 10, 12, 0))
        )
        self.oak_event.refresh_from_db()
        self.pine_event.refresh_from_db()
        self.client.force_login(self.staff_user)

        response = self.client.get(
            reverse('core:activity_list'),
            {'from': '2026-07-05', 'to': '2026-07-15'},
        )

        self.assertEqual(
            list(response.context['activity_events']),
            [self.pine_event],
        )
        self.assertEqual(response.context['activity_from_date'], date(2026, 7, 5))
        self.assertEqual(response.context['activity_to_date'], date(2026, 7, 15))
        self.assertContains(response, 'value="2026-07-05"')
        self.assertContains(response, 'value="2026-07-15"')
        self.assertContains(
            response,
            'from=2026-07-05&amp;to=2026-07-15',
        )

        invalid_response = self.client.get(
            reverse('core:activity_list'),
            {'from': 'not-a-date', 'to': '2026-99-99'},
        )
        self.assertIsNone(invalid_response.context['activity_from_date'])
        self.assertIsNone(invalid_response.context['activity_to_date'])
        self.assertFalse(invalid_response.context['has_activity_filters'])
        self.assertEqual(invalid_response.context['page_obj'].paginator.count, 2)

    def test_activity_list_paginates_and_retains_filters(self):
        for number in range(1, 27):
            ActivityEvent.objects.create(
                organization=self.organization,
                project=self.oak_project,
                actor=self.staff_user,
                event_type=ActivityEvent.Type.MESSAGE_SENT,
                summary=f'Pagination event {number:02d}.',
            )
        self.client.force_login(self.staff_user)
        response = self.client.get(
            reverse('core:activity_list'),
            {'q': 'event', 'page': 1},
        )

        self.assertEqual(response.context['page_obj'].paginator.count, 26)
        self.assertEqual(len(response.context['activity_events']), 25)
        self.assertContains(response, 'Page 1 of 2')
        self.assertContains(response, 'q=event&amp;page=2')

        unfiltered_response = self.client.get(reverse('core:activity_list'))
        self.assertEqual(unfiltered_response.context['page_obj'].paginator.count, 28)
        self.assertEqual(len(unfiltered_response.context['activity_events']), 25)
        self.assertContains(unfiltered_response, 'Page 1 of 2')
        self.assertContains(unfiltered_response, 'page=2')

        second_page = self.client.get(
            reverse('core:activity_list'),
            {'page': 2},
        )
        self.assertEqual(len(second_page.context['activity_events']), 3)
        self.assertContains(second_page, 'Page 2 of 2')

    def test_activity_empty_and_filtered_empty_states(self):
        ActivityEvent.objects.all().delete()
        self.client.force_login(self.staff_user)

        empty_response = self.client.get(reverse('core:activity_list'))
        self.assertContains(empty_response, 'No project activity yet')

        filtered_response = self.client.get(
            reverse('core:activity_list'),
            {'q': 'nothing'},
        )
        self.assertContains(filtered_response, 'No activity matches these filters')

    def test_dashboard_links_to_full_activity_page_for_staff_only(self):
        self.client.force_login(self.staff_user)
        staff_response = self.client.get(reverse('core:home'))
        self.assertContains(staff_response, reverse('core:activity_list'))
        self.assertContains(staff_response, 'View all activity')

        self.client.force_login(self.client_user)
        client_response = self.client.get(reverse('core:home'))
        self.assertNotContains(client_response, reverse('core:activity_list'))

    def test_activity_export_requires_management_access(self):
        url = reverse('core:activity_export')
        anonymous_response = self.client.get(url)
        self.assertEqual(anonymous_response.status_code, 302)
        self.assertIn(reverse('login'), anonymous_response.url)

        for user in (self.accountant, self.client_user, self.subcontractor):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_activity_export_streams_authorized_rows_without_metadata(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse('core:activity_export'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn(
            f'project-activity-{timezone.localdate().isoformat()}.csv',
            response['Content-Disposition'],
        )
        content = b''.join(response.streaming_content).decode('utf-8')
        rows = list(csv.reader(io.StringIO(content)))

        self.assertEqual(
            rows[0],
            [
                'Timestamp',
                'Company',
                'Project',
                'Project code',
                'Event type',
                'Event label',
                'Summary',
                'Actor email',
            ],
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            {row[6] for row in rows[1:]},
            {
                'Foundation plan was added.',
                'Pine Street schedule was updated.',
            },
        )
        self.assertTrue(all(row[1] == 'Example Builders' for row in rows[1:]))
        self.assertTrue(all(row[7] == self.staff_user.email for row in rows[1:]))
        self.assertNotIn('Hidden activity must not appear.', content)
        self.assertNotIn('Company-only activity must not appear.', content)
        self.assertNotIn('98765.43', content)

    def test_activity_export_applies_the_same_filters_as_the_page(self):
        ActivityEvent.objects.filter(pk=self.oak_event.pk).update(
            created_at=timezone.make_aware(datetime(2026, 7, 1, 12, 0))
        )
        ActivityEvent.objects.filter(pk=self.pine_event.pk).update(
            created_at=timezone.make_aware(datetime(2026, 7, 10, 12, 0))
        )
        self.client.force_login(self.staff_user)
        filters = {
            'q': 'pine',
            'project': self.pine_project.pk,
            'type': ActivityEvent.Type.PROJECT_UPDATED,
            'from': '2026-07-05',
            'to': '2026-07-15',
        }

        page_response = self.client.get(reverse('core:activity_list'), filters)
        export_response = self.client.get(
            reverse('core:activity_export'),
            filters,
        )
        rows = list(
            csv.reader(
                io.StringIO(
                    b''.join(export_response.streaming_content).decode('utf-8')
                )
            )
        )

        self.assertEqual(page_response.context['page_obj'].paginator.count, 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][2], 'Pine Street')
        self.assertEqual(rows[1][6], 'Pine Street schedule was updated.')

    def test_activity_export_neutralizes_spreadsheet_formulas(self):
        self.oak_project.code = '-DANGEROUS'
        self.oak_project.save(update_fields=['code'])
        ActivityEvent.objects.create(
            organization=self.organization,
            project=self.oak_project,
            actor=self.staff_user,
            event_type=ActivityEvent.Type.MESSAGE_SENT,
            summary='  =HYPERLINK("https://example.invalid")',
        )
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse('core:activity_export'))
        rows = list(
            csv.reader(
                io.StringIO(b''.join(response.streaming_content).decode('utf-8'))
            )
        )
        formula_row = next(row for row in rows if 'HYPERLINK' in row[6])

        self.assertEqual(formula_row[3], "'-DANGEROUS")
        self.assertEqual(
            formula_row[6],
            "'  =HYPERLINK(\"https://example.invalid\")",
        )
