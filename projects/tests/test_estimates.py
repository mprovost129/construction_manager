from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from projects.models import (
    ActivityEvent,
    CostCode,
    Estimate,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
)
from projects.tests import grant_internal_access


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EstimateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(
            name='Example Builders', slug='example-builders-est'
        )
        cls.project = Project.objects.create(
            organization=cls.organization,
            name='Oak Street',
            status=Project.Status.ACTIVE,
        )
        cls.admin_user = get_user_model().objects.create_user(
            'admin-est@example.com', 'password'
        )
        cls.accountant = get_user_model().objects.create_user(
            'accountant-est@example.com', 'password'
        )
        cls.staff_user = get_user_model().objects.create_user(
            'staff-est@example.com', 'password'
        )
        cls.client_user = get_user_model().objects.create_user(
            'client-est@example.com', 'password'
        )
        cls.second_client = get_user_model().objects.create_user(
            'second-client-est@example.com', 'password'
        )
        for user, role in (
            (cls.admin_user, OrganizationMembership.Role.ADMIN),
            (cls.accountant, OrganizationMembership.Role.ACCOUNTANT),
            (cls.staff_user, OrganizationMembership.Role.STAFF),
            (cls.client_user, OrganizationMembership.Role.CLIENT),
            (cls.second_client, OrganizationMembership.Role.CLIENT),
        ):
            OrganizationMembership.objects.create(
                organization=cls.organization, user=user, role=role
            )
        for user in (cls.client_user, cls.second_client):
            ProjectMembership.objects.create(
                project=cls.project,
                user=user,
                role=OrganizationMembership.Role.CLIENT,
            )
        grant_internal_access(cls.staff_user, cls.project)
        grant_internal_access(
            cls.accountant, cls.project, can_manage=False, can_invite_clients=False
        )

    def create_estimate(self, *, title='Full home build', required_approvals=1):
        return Estimate.objects.create(
            project=self.project,
            number=self.project.estimates.count() + 1,
            title=title,
            description='Full construction pricing.',
            created_by=self.admin_user,
            required_approvals=required_approvals,
        )

    def add_line(self, estimate, *, price='100.00', cost='60.00'):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse(
                'projects:estimate_line_item_create',
                args=(self.project.pk, estimate.pk),
            ),
            {
                'category': 'material',
                'description': 'Lumber package',
                'quantity': '1',
                'unit_price': price,
                'unit_cost': cost,
                'sort_order': '0',
            },
        )
        self.assertEqual(response.status_code, 302)
        estimate.refresh_from_db()
        return estimate

    def test_admin_creates_sequential_draft_estimates(self):
        self.client.force_login(self.admin_user)
        first = self.client.post(
            reverse('projects:estimate_create', args=(self.project.pk,)),
            {'title': 'First pass', 'description': 'Initial pricing.'},
        )
        second = self.client.post(
            reverse('projects:estimate_create', args=(self.project.pk,)),
            {'title': 'Second pass', 'description': 'Revised pricing.'},
        )
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        numbers = list(self.project.estimates.order_by('number').values_list('number', flat=True))
        self.assertEqual(numbers, [1, 2])

    def test_accountant_can_view_but_not_manage_estimates(self):
        estimate = self.create_estimate()
        self.client.force_login(self.accountant)
        response = self.client.get(
            reverse('projects:estimate_detail', args=(self.project.pk, estimate.pk))
        )
        self.assertEqual(response.status_code, 200)
        edit_response = self.client.get(
            reverse('projects:estimate_edit', args=(self.project.pk, estimate.pk))
        )
        self.assertEqual(edit_response.status_code, 403)

    def test_submit_requires_line_items(self):
        estimate = self.create_estimate()
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse('projects:estimate_submit', args=(self.project.pk, estimate.pk))
        )
        self.assertEqual(response.status_code, 403)

    def test_approval_sets_contract_amount_and_locks_it(self):
        estimate = self.create_estimate()
        estimate = self.add_line(estimate, price='500.00', cost='300.00')
        self.assertEqual(estimate.price_total, Decimal('500.00'))
        self.assertEqual(estimate.cost_total, Decimal('300.00'))

        self.client.force_login(self.admin_user)
        submit_response = self.client.post(
            reverse('projects:estimate_submit', args=(self.project.pk, estimate.pk))
        )
        self.assertEqual(submit_response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

        self.client.force_login(self.client_user)
        decision_response = self.client.post(
            reverse('projects:estimate_decision', args=(self.project.pk, estimate.pk)),
            {'decision': Estimate.Status.APPROVED, 'comment': 'Looks good.'},
        )
        self.assertEqual(decision_response.status_code, 302)

        estimate.refresh_from_db()
        self.project.refresh_from_db()
        self.assertEqual(estimate.status, Estimate.Status.APPROVED)
        self.assertEqual(self.project.contract_amount, Decimal('500.00'))
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.ESTIMATE_DECIDED,
            ).exists()
        )

        # A second estimate cannot be submitted once the contract amount is set.
        second = self.create_estimate(title='Follow-up pricing')
        second = self.add_line(second, price='100.00', cost='50.00')
        second_submit = self.client.post(
            reverse('projects:estimate_submit', args=(self.project.pk, second.pk))
        )
        self.assertEqual(second_submit.status_code, 403)

    def test_multi_approval_requires_distinct_clients(self):
        estimate = self.create_estimate(required_approvals=2)
        estimate = self.add_line(estimate)
        self.client.force_login(self.admin_user)
        self.client.post(
            reverse('projects:estimate_submit', args=(self.project.pk, estimate.pk))
        )

        self.client.force_login(self.client_user)
        first_decision = self.client.post(
            reverse('projects:estimate_decision', args=(self.project.pk, estimate.pk)),
            {'decision': Estimate.Status.APPROVED, 'comment': ''},
        )
        self.assertEqual(first_decision.status_code, 302)
        estimate.refresh_from_db()
        self.assertEqual(estimate.status, Estimate.Status.PENDING)
        self.project.refresh_from_db()
        self.assertIsNone(self.project.contract_amount)

        self.client.force_login(self.second_client)
        second_decision = self.client.post(
            reverse('projects:estimate_decision', args=(self.project.pk, estimate.pk)),
            {'decision': Estimate.Status.APPROVED, 'comment': ''},
        )
        self.assertEqual(second_decision.status_code, 302)
        estimate.refresh_from_db()
        self.project.refresh_from_db()
        self.assertEqual(estimate.status, Estimate.Status.APPROVED)
        self.assertIsNotNone(self.project.contract_amount)

    def test_declined_estimate_does_not_set_contract_amount(self):
        estimate = self.create_estimate()
        estimate = self.add_line(estimate)
        self.client.force_login(self.admin_user)
        self.client.post(
            reverse('projects:estimate_submit', args=(self.project.pk, estimate.pk))
        )
        self.client.force_login(self.client_user)
        response = self.client.post(
            reverse('projects:estimate_decision', args=(self.project.pk, estimate.pk)),
            {'decision': Estimate.Status.DECLINED, 'comment': 'Too expensive.'},
        )
        self.assertEqual(response.status_code, 302)
        estimate.refresh_from_db()
        self.project.refresh_from_db()
        self.assertEqual(estimate.status, Estimate.Status.DECLINED)
        self.assertIsNone(self.project.contract_amount)

    def test_pending_estimate_can_be_voided_by_staff(self):
        estimate = self.create_estimate()
        estimate = self.add_line(estimate)
        self.client.force_login(self.admin_user)
        self.client.post(
            reverse('projects:estimate_submit', args=(self.project.pk, estimate.pk))
        )
        response = self.client.post(
            reverse('projects:estimate_void', args=(self.project.pk, estimate.pk)),
            {'reason': 'Client asked to restart.'},
        )
        self.assertEqual(response.status_code, 302)
        estimate.refresh_from_db()
        self.assertEqual(estimate.status, Estimate.Status.VOIDED)

    def test_line_items_use_organization_scoped_cost_codes(self):
        estimate = self.create_estimate()
        cost_code = CostCode.objects.create(
            organization=self.organization, code='01-100', name='Sitework'
        )
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse(
                'projects:estimate_line_item_create',
                args=(self.project.pk, estimate.pk),
            ),
            {
                'category': 'material',
                'cost_code': cost_code.pk,
                'description': 'Grading',
                'quantity': '1',
                'unit_price': '1000.00',
                'unit_cost': '700.00',
                'sort_order': '0',
            },
        )
        self.assertEqual(response.status_code, 302)
        line_item = estimate.line_items.get()
        self.assertEqual(line_item.cost_code, cost_code)

    def test_client_cannot_see_draft_estimates(self):
        self.create_estimate()
        self.client.force_login(self.client_user)
        response = self.client.get(
            reverse('projects:estimate_list', args=(self.project.pk,))
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'EST-001')
