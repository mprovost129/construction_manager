from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from projects.models import (
    ChangeOrder,
    ConversationThread,
    FinishSelection,
    Organization,
    OrganizationMembership,
    Project,
    ProjectDocument,
    ProjectDocumentVersion,
    ProjectMembership,
    ScheduleMilestone,
)


class PortfolioDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(
            name='Example Builders',
            slug='example-builders',
        )
        cls.priority_project = Project.objects.create(
            organization=cls.organization,
            name='Oak Street',
            status=Project.Status.ACTIVE,
        )
        cls.clear_project = Project.objects.create(
            organization=cls.organization,
            name='Pine Street',
        )
        other_organization = Organization.objects.create(
            name='Other Builders',
            slug='other-builders',
        )
        cls.hidden_project = Project.objects.create(
            organization=other_organization,
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
        cls.clear_client = user_model.objects.create_user(
            'clear@example.com',
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
            (cls.clear_client, OrganizationMembership.Role.CLIENT),
            (cls.subcontractor, OrganizationMembership.Role.SUBCONTRACTOR),
        ):
            OrganizationMembership.objects.create(
                organization=cls.organization,
                user=user,
                role=role,
            )

        for user, project, role in (
            (
                cls.client_user,
                cls.priority_project,
                OrganizationMembership.Role.CLIENT,
            ),
            (
                cls.clear_client,
                cls.clear_project,
                OrganizationMembership.Role.CLIENT,
            ),
            (
                cls.subcontractor,
                cls.priority_project,
                OrganizationMembership.Role.SUBCONTRACTOR,
            ),
        ):
            ProjectMembership.objects.create(
                project=project,
                user=user,
                role=role,
            )

        ChangeOrder.objects.create(
            project=cls.priority_project,
            number=1,
            title='Draft addition',
            description='Draft scope.',
            price_delta=Decimal('1000.00'),
            cost_delta=Decimal('600.00'),
            created_by=cls.staff_user,
        )
        ChangeOrder.objects.create(
            project=cls.priority_project,
            number=2,
            title='Pending addition',
            description='Pending client scope.',
            price_delta=Decimal('2000.00'),
            cost_delta=Decimal('1200.00'),
            status=ChangeOrder.Status.PENDING,
            created_by=cls.staff_user,
            submitted_by=cls.staff_user,
            submitted_at=timezone.now(),
        )
        FinishSelection.objects.create(
            project=cls.priority_project,
            number=1,
            title='Cabinet color',
            allowance_amount=Decimal('3000.00'),
            due_date=date(2020, 1, 1),
            status=FinishSelection.Status.OPEN,
            created_by=cls.staff_user,
            opened_by=cls.staff_user,
            opened_at=timezone.now(),
        )
        document = ProjectDocument.objects.create(
            project=cls.priority_project,
            title='Foundation plan',
            client_visible=True,
            requires_client_approval=True,
            created_by=cls.staff_user,
        )
        ProjectDocumentVersion.objects.create(
            document=document,
            version_number=1,
            file='project_documents/test/foundation.pdf',
            original_filename='foundation.pdf',
            uploaded_by=cls.staff_user,
        )
        ScheduleMilestone.objects.create(
            project=cls.priority_project,
            title='Visible delay',
            start_date=date(2026, 8, 1),
            status=ScheduleMilestone.Status.DELAYED,
            client_visible=True,
            created_by=cls.staff_user,
        )
        ScheduleMilestone.objects.create(
            project=cls.priority_project,
            title='Internal delay',
            start_date=date(2026, 8, 2),
            status=ScheduleMilestone.Status.DELAYED,
            client_visible=False,
            created_by=cls.staff_user,
        )
        ConversationThread.objects.create(
            project=cls.priority_project,
            subject='Open question',
            created_by=cls.staff_user,
        )
        ChangeOrder.objects.create(
            project=cls.hidden_project,
            number=1,
            title='Hidden draft',
            description='Must never appear.',
            created_by=cls.staff_user,
        )

    def test_staff_dashboard_rolls_up_accessible_project_actions(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse('core:home'))

        portfolio = response.context['portfolio_action_center']
        self.assertEqual(portfolio['project_count'], 2)
        self.assertEqual(portfolio['projects_with_attention_count'], 1)
        self.assertEqual(portfolio['decision_count'], 3)
        self.assertEqual(portfolio['draft_count'], 1)
        self.assertEqual(portfolio['schedule_count'], 2)
        self.assertEqual(portfolio['conversation_count'], 1)
        self.assertEqual(portfolio['action_count'], 6)
        self.assertTrue(portfolio['viewer_has_internal_scope'])
        self.assertContains(response, 'Project priorities')
        self.assertContains(response, 'items needing attention')
        self.assertContains(response, 'Drafts to finish')
        self.assertContains(response, 'Oak Street')
        self.assertContains(response, 'Pine Street')
        self.assertNotContains(response, 'Hidden Project')

    def test_client_dashboard_uses_client_visibility_and_personal_decisions(self):
        self.client.force_login(self.client_user)
        response = self.client.get(reverse('core:home'))

        portfolio = response.context['portfolio_action_center']
        self.assertEqual(portfolio['project_count'], 1)
        self.assertEqual(portfolio['decision_count'], 3)
        self.assertEqual(portfolio['draft_count'], 0)
        self.assertEqual(portfolio['schedule_count'], 1)
        self.assertEqual(portfolio['conversation_count'], 1)
        self.assertEqual(portfolio['action_count'], 4)
        self.assertFalse(portfolio['viewer_has_internal_scope'])
        self.assertContains(response, 'Your decisions')
        self.assertContains(response, 'items needing attention')
        self.assertNotContains(response, 'Drafts to finish')
        self.assertNotContains(response, 'Pine Street')

    def test_conversation_only_project_is_included_in_priority_list(self):
        ConversationThread.objects.create(
            project=self.clear_project,
            subject='Pine Street question',
            created_by=self.staff_user,
        )
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse('core:home'))

        portfolio = response.context['portfolio_action_center']
        self.assertEqual(portfolio['projects_with_attention_count'], 2)
        self.assertEqual(portfolio['conversation_count'], 2)
        pine_summary = next(
            summary
            for summary in portfolio['priority_projects']
            if summary['project'] == self.clear_project
        )
        self.assertEqual(pine_summary['action_count'], 0)
        self.assertEqual(pine_summary['conversation_count'], 1)
        self.assertContains(response, '1 open conversation')

    def test_accountant_and_subcontractor_keep_project_access_without_rollup(self):
        for user in (self.accountant, self.subcontractor):
            self.client.force_login(user)
            response = self.client.get(reverse('core:home'))
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('portfolio_action_center', response.context)
            self.assertNotContains(response, 'Action overview')
            self.assertContains(response, 'Oak Street')

        self.client.force_login(self.subcontractor)
        subcontractor_response = self.client.get(reverse('core:home'))
        self.assertNotContains(subcontractor_response, 'Pine Street')

    def test_authorized_user_with_clear_project_sees_caught_up_state(self):
        self.client.force_login(self.clear_client)
        response = self.client.get(reverse('core:home'))

        portfolio = response.context['portfolio_action_center']
        self.assertEqual(portfolio['action_count'], 0)
        self.assertEqual(portfolio['conversation_count'], 0)
        self.assertContains(response, 'Everything is caught up')
        self.assertContains(response, 'Caught up')
