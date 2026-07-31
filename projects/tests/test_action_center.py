from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from projects.models import (
    ChangeOrder,
    ConversationThread,
    DocumentDecision,
    FinishSelection,
    Organization,
    OrganizationMembership,
    Project,
    ProjectDocument,
    ProjectDocumentVersion,
    ProjectMembership,
    ScheduleMilestone,
    SelectionOption,
)
from projects.tests import grant_internal_access


class ProjectActionCenterTests(TestCase):
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

        cls.draft_change_order = ChangeOrder.objects.create(
            project=cls.project,
            number=1,
            title='Draft roof revision',
            description='Revise the rear roof line.',
            price_delta=Decimal('1500.00'),
            cost_delta=Decimal('900.00'),
            created_by=cls.admin_user,
        )
        cls.pending_change_order = ChangeOrder.objects.create(
            project=cls.project,
            number=2,
            title='Pending porch addition',
            description='Add a covered rear porch.',
            price_delta=Decimal('5000.00'),
            cost_delta=Decimal('3000.00'),
            status=ChangeOrder.Status.PENDING,
            created_by=cls.admin_user,
            submitted_by=cls.staff_user,
            submitted_at=timezone.now(),
        )
        ChangeOrder.objects.create(
            project=cls.project,
            number=3,
            title='Approved window package',
            description='Upgrade the window package.',
            status=ChangeOrder.Status.APPROVED,
            created_by=cls.admin_user,
            submitted_by=cls.admin_user,
            submitted_at=timezone.now(),
            decided_by=cls.client_user,
            decided_at=timezone.now(),
        )

        cls.draft_selection = FinishSelection.objects.create(
            project=cls.project,
            number=1,
            title='Draft flooring package',
            allowance_amount=Decimal('2000.00'),
            created_by=cls.admin_user,
        )
        cls.open_selection = FinishSelection.objects.create(
            project=cls.project,
            number=2,
            title='Overdue cabinet finish',
            allowance_amount=Decimal('3000.00'),
            due_date=date(2020, 1, 1),
            status=FinishSelection.Status.OPEN,
            created_by=cls.admin_user,
            opened_by=cls.staff_user,
            opened_at=timezone.now(),
        )
        SelectionOption.objects.create(
            selection=cls.open_selection,
            name='Natural oak',
            price=Decimal('3200.00'),
            cost=Decimal('2100.00'),
        )

        cls.pending_document = ProjectDocument.objects.create(
            project=cls.project,
            title='Pending foundation plan',
            client_visible=True,
            requires_client_approval=True,
            created_by=cls.admin_user,
        )
        cls.pending_document_version = ProjectDocumentVersion.objects.create(
            document=cls.pending_document,
            version_number=1,
            file='project_documents/test/pending.pdf',
            original_filename='pending.pdf',
            uploaded_by=cls.admin_user,
        )
        cls.declined_document = ProjectDocument.objects.create(
            project=cls.project,
            title='Declined elevation plan',
            client_visible=True,
            requires_client_approval=True,
            created_by=cls.admin_user,
        )
        declined_version = ProjectDocumentVersion.objects.create(
            document=cls.declined_document,
            version_number=1,
            file='project_documents/test/declined.pdf',
            original_filename='declined.pdf',
            uploaded_by=cls.admin_user,
        )
        DocumentDecision.objects.create(
            version=declined_version,
            decided_by=cls.client_user,
            decision=DocumentDecision.Decision.DECLINED,
            comment='Please revise this elevation.',
        )

        cls.visible_delay = ScheduleMilestone.objects.create(
            project=cls.project,
            title='Delayed framing',
            start_date=date(2026, 8, 1),
            status=ScheduleMilestone.Status.DELAYED,
            client_visible=True,
            internal_notes='Crew reassignment details.',
            created_by=cls.admin_user,
        )
        cls.internal_delay = ScheduleMilestone.objects.create(
            project=cls.project,
            title='Internal inspection delay',
            start_date=date(2026, 8, 5),
            status=ScheduleMilestone.Status.DELAYED,
            client_visible=False,
            internal_notes='Internal inspection coordination.',
            created_by=cls.admin_user,
        )
        cls.open_thread = ConversationThread.objects.create(
            project=cls.project,
            subject='Open cabinet question',
            created_by=cls.admin_user,
        )
        ConversationThread.objects.create(
            project=cls.project,
            subject='Closed framing question',
            status=ConversationThread.Status.CLOSED,
            created_by=cls.admin_user,
        )

    def test_internal_action_center_aggregates_all_operational_priorities(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(
            reverse('projects:action_center', args=(self.project.pk,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['decision_count'], 4)
        self.assertEqual(response.context['draft_count'], 2)
        self.assertEqual(response.context['schedule_count'], 2)
        self.assertEqual(response.context['conversation_count'], 1)
        self.assertEqual(response.context['action_count'], 8)
        for expected in (
            'Draft roof revision',
            'Pending porch addition',
            'Draft flooring package',
            'Overdue cabinet finish',
            'Pending foundation plan',
            'Declined elevation plan',
            'Delayed framing',
            'Internal inspection delay',
            'Open cabinet question',
        ):
            self.assertContains(response, expected)
        self.assertNotContains(response, 'Approved window package')
        self.assertNotContains(response, 'Closed framing question')

    def test_client_action_center_excludes_drafts_internal_items_and_own_decisions(self):
        self.client.force_login(self.client_user)
        response = self.client.get(
            reverse('projects:action_center', args=(self.project.pk,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['decision_count'], 3)
        self.assertEqual(response.context['draft_count'], 0)
        self.assertEqual(response.context['schedule_count'], 1)
        self.assertEqual(response.context['action_count'], 4)
        for expected in (
            'Pending porch addition',
            'Overdue cabinet finish',
            'Pending foundation plan',
            'Delayed framing',
            'Open cabinet question',
        ):
            self.assertContains(response, expected)
        for hidden in (
            'Draft roof revision',
            'Draft flooring package',
            'Declined elevation plan',
            'Internal inspection delay',
            'Crew reassignment details',
            '2100.00',
        ):
            self.assertNotContains(response, hidden)

    def test_client_document_action_disappears_after_their_decision(self):
        DocumentDecision.objects.create(
            version=self.pending_document_version,
            decided_by=self.client_user,
            decision=DocumentDecision.Decision.APPROVED,
        )
        self.client.force_login(self.client_user)
        response = self.client.get(
            reverse('projects:action_center', args=(self.project.pk,))
        )

        self.assertNotContains(response, self.pending_document.title)
        self.assertEqual(response.context['decision_count'], 2)
        self.assertEqual(response.context['action_count'], 3)

    def test_project_page_shows_role_appropriate_action_count(self):
        self.client.force_login(self.staff_user)
        internal_response = self.client.get(
            reverse('projects:detail', args=(self.project.pk,))
        )
        self.assertContains(internal_response, '8 items need attention')

        self.client.force_login(self.client_user)
        client_response = self.client.get(
            reverse('projects:detail', args=(self.project.pk,))
        )
        self.assertContains(client_response, '4 items need attention')

    def test_accountant_and_subcontractor_cannot_access_action_center(self):
        url = reverse('projects:action_center', args=(self.project.pk,))
        for user in (self.accountant, self.subcontractor):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_client_cannot_access_action_center_for_unassigned_project(self):
        self.client.force_login(self.client_user)
        response = self.client.get(
            reverse('projects:action_center', args=(self.other_project.pk,))
        )
        self.assertEqual(response.status_code, 404)

    def test_empty_project_shows_clear_state(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse('projects:action_center', args=(self.other_project.pk,))
        )
        self.assertContains(response, 'No items need immediate attention')
        self.assertEqual(response.context['action_count'], 0)
