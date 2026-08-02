from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from projects.models import (
    ActivityEvent,
    ChangeOrder,
    CostCode,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
)
from projects.tests import grant_internal_access


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ChangeOrderTests(TestCase):
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

    def create_change_order(self, *, project=None, status=ChangeOrder.Status.DRAFT):
        project = project or self.project
        data = {
            'project': project,
            'number': 1,
            'title': 'Add screened porch',
            'description': 'Frame and finish a screened porch at the rear elevation.',
            'reason': 'Requested after contract execution.',
            'price_delta': Decimal('2500.00'),
            'cost_delta': Decimal('900.00'),
            'schedule_delta_days': 4,
            'status': status,
            'created_by': self.admin_user,
        }
        if status != ChangeOrder.Status.DRAFT:
            data.update(
                {
                    'submitted_by': self.admin_user,
                    'submitted_at': timezone.now(),
                }
            )
        if status in (ChangeOrder.Status.APPROVED, ChangeOrder.Status.DECLINED):
            data.update(
                {
                    'decided_by': self.client_user,
                    'decided_at': timezone.now(),
                }
            )
        return ChangeOrder.objects.create(**data)

    def form_data(self, title='Add screened porch'):
        return {
            'title': title,
            'description': 'Frame and finish a screened porch.',
            'reason': 'Owner request.',
            'price_delta': '2500.00',
            'cost_delta': '900.00',
            'schedule_delta_days': '4',
        }

    def test_admin_creates_sequential_drafts_with_audit_events(self):
        self.client.force_login(self.admin_user)
        first_response = self.client.post(
            reverse('projects:change_order_create', args=(self.project.pk,)),
            self.form_data(),
        )
        second_response = self.client.post(
            reverse('projects:change_order_create', args=(self.project.pk,)),
            self.form_data('Upgrade fireplace surround'),
        )

        first, second = ChangeOrder.objects.order_by('number')
        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(first.display_number, 'CO-001')
        self.assertEqual(second.display_number, 'CO-002')
        self.assertEqual(first.status, ChangeOrder.Status.DRAFT)
        self.assertEqual(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.CHANGE_ORDER_CREATED
            ).count(),
            2,
        )

    def test_draft_is_hidden_from_clients(self):
        change_order = self.create_change_order()
        self.client.force_login(self.client_user)

        list_response = self.client.get(
            reverse('projects:change_order_list', args=(self.project.pk,))
        )
        detail_response = self.client.get(
            reverse(
                'projects:change_order_detail',
                args=(self.project.pk, change_order.pk),
            )
        )

        self.assertNotContains(list_response, change_order.title)
        self.assertEqual(detail_response.status_code, 404)

    def test_submit_notifies_all_active_clients_and_locks_editing(self):
        change_order = self.create_change_order()
        self.client.force_login(self.staff_user)
        response = self.client.post(
            reverse(
                'projects:change_order_submit',
                args=(self.project.pk, change_order.pk),
            )
        )
        change_order.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(change_order.status, ChangeOrder.Status.PENDING)
        self.assertEqual(change_order.submitted_by, self.staff_user)
        self.assertIsNotNone(change_order.submitted_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].to,
            ['client@example.com', 'second-client@example.com'],
        )
        edit_response = self.client.get(
            reverse(
                'projects:change_order_edit',
                args=(self.project.pk, change_order.pk),
            )
        )
        self.assertEqual(edit_response.status_code, 403)

    def test_change_order_cannot_be_submitted_without_active_client(self):
        change_order = self.create_change_order(project=self.other_project)
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse(
                'projects:change_order_submit',
                args=(self.other_project.pk, change_order.pk),
            ),
            follow=True,
        )
        change_order.refresh_from_db()

        self.assertEqual(change_order.status, ChangeOrder.Status.DRAFT)
        self.assertContains(response, 'Assign an active client')
        self.assertEqual(len(mail.outbox), 0)

    def test_client_sees_price_and_schedule_but_not_internal_cost_or_margin(self):
        change_order = self.create_change_order(status=ChangeOrder.Status.PENDING)
        self.client.force_login(self.client_user)
        response = self.client.get(
            reverse(
                'projects:change_order_detail',
                args=(self.project.pk, change_order.pk),
            )
        )

        self.assertContains(response, '$2500.00')
        self.assertContains(response, '4 days')
        self.assertNotContains(response, '$900.00')
        self.assertNotContains(response, 'Estimated margin impact')
        self.assertNotContains(response, 'QuickBooks Online')
        self.assertContains(response, 'Record decision')

    def test_staff_sees_internal_cost_margin_and_quickbooks_handoff(self):
        change_order = self.create_change_order(status=ChangeOrder.Status.APPROVED)
        self.client.force_login(self.staff_user)
        response = self.client.get(
            reverse(
                'projects:change_order_detail',
                args=(self.project.pk, change_order.pk),
            )
        )

        self.assertContains(response, '$900.00')
        self.assertContains(response, '$1600.00')
        self.assertContains(response, 'Send to QuickBooks when invoiced')
        self.assertContains(response, 'QuickBooks remains the accounting source of truth')

    def test_first_client_decision_is_authenticated_audited_and_locked(self):
        change_order = self.create_change_order(status=ChangeOrder.Status.PENDING)
        self.client.force_login(self.client_user)
        response = self.client.post(
            reverse(
                'projects:change_order_decision',
                args=(self.project.pk, change_order.pk),
            ),
            {'decision': ChangeOrder.Status.APPROVED, 'comment': 'Proceed.'},
        )
        change_order.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(change_order.status, ChangeOrder.Status.APPROVED)
        self.assertEqual(change_order.decided_by, self.client_user)
        self.assertEqual(change_order.client_comment, 'Proceed.')
        self.assertEqual(
            mail.outbox[0].to, ['admin@example.com', 'staff@example.com']
        )
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.CHANGE_ORDER_DECIDED,
                actor=self.client_user,
            ).exists()
        )

        self.client.force_login(self.second_client)
        self.client.post(
            reverse(
                'projects:change_order_decision',
                args=(self.project.pk, change_order.pk),
            ),
            {'decision': ChangeOrder.Status.DECLINED},
        )
        change_order.refresh_from_db()
        self.assertEqual(change_order.status, ChangeOrder.Status.APPROVED)
        self.assertEqual(change_order.decided_by, self.client_user)
        self.assertEqual(len(mail.outbox), 1)

    def test_staff_accountant_and_subcontractor_cannot_make_client_decision(self):
        change_order = self.create_change_order(status=ChangeOrder.Status.PENDING)
        decision_url = reverse(
            'projects:change_order_decision',
            args=(self.project.pk, change_order.pk),
        )
        for user in (self.staff_user, self.accountant, self.subcontractor):
            self.client.force_login(user)
            response = self.client.post(
                decision_url, {'decision': ChangeOrder.Status.APPROVED}
            )
            self.assertEqual(response.status_code, 403)
        change_order.refresh_from_db()
        self.assertEqual(change_order.status, ChangeOrder.Status.PENDING)

    def test_accountant_and_subcontractor_cannot_access_change_orders(self):
        url = reverse('projects:change_order_list', args=(self.project.pk,))
        for user in (self.accountant, self.subcontractor):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_voided_request_notifies_clients_and_cannot_be_decided(self):
        change_order = self.create_change_order(status=ChangeOrder.Status.PENDING)
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse(
                'projects:change_order_void',
                args=(self.project.pk, change_order.pk),
            ),
            {'reason': 'Client asked to hold off.'},
        )
        change_order.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(change_order.status, ChangeOrder.Status.VOIDED)
        self.assertEqual(change_order.voided_by, self.admin_user)
        self.assertEqual(change_order.void_reason, 'Client asked to hold off.')
        self.assertFalse(change_order.requires_financial_reversal)
        self.assertEqual(
            mail.outbox[0].to,
            ['client@example.com', 'second-client@example.com'],
        )

        self.client.force_login(self.client_user)
        self.client.post(
            reverse(
                'projects:change_order_decision',
                args=(self.project.pk, change_order.pk),
            ),
            {'decision': ChangeOrder.Status.APPROVED},
        )
        change_order.refresh_from_db()
        self.assertEqual(change_order.status, ChangeOrder.Status.VOIDED)
        self.assertEqual(len(mail.outbox), 1)

    def test_void_without_reason_is_rejected(self):
        change_order = self.create_change_order(status=ChangeOrder.Status.PENDING)
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse(
                'projects:change_order_void',
                args=(self.project.pk, change_order.pk),
            ),
            {'reason': ''},
        )
        change_order.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(change_order.status, ChangeOrder.Status.PENDING)

    def test_approved_change_order_can_be_voided_with_reversal_flag(self):
        change_order = self.create_change_order(status=ChangeOrder.Status.APPROVED)
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse(
                'projects:change_order_void',
                args=(self.project.pk, change_order.pk),
            ),
            {'reason': 'Scope removed after approval.'},
        )
        change_order.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(change_order.status, ChangeOrder.Status.VOIDED)
        self.assertTrue(change_order.requires_financial_reversal)
        self.assertEqual(change_order.void_reason, 'Scope removed after approval.')

    def test_draft_change_order_cannot_be_voided(self):
        change_order = self.create_change_order(status=ChangeOrder.Status.DRAFT)
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse(
                'projects:change_order_void',
                args=(self.project.pk, change_order.pk),
            ),
            {'reason': 'Not applicable.'},
        )
        self.assertEqual(response.status_code, 403)
        change_order.refresh_from_db()
        self.assertEqual(change_order.status, ChangeOrder.Status.DRAFT)

    def test_line_items_recalculate_totals_and_lock_fields(self):
        self.client.force_login(self.admin_user)
        change_order = self.create_change_order(status=ChangeOrder.Status.DRAFT)
        cost_code = CostCode.objects.create(
            organization=self.organization, code='06-100', name='Decking'
        )
        add_url = reverse(
            'projects:change_order_line_item_create',
            args=(self.project.pk, change_order.pk),
        )
        response = self.client.post(
            add_url,
            {
                'category': 'material',
                'cost_code': cost_code.pk,
                'description': 'Cedar decking',
                'quantity': '10',
                'unit_price': '25.00',
                'unit_cost': '15.00',
                'sort_order': '0',
            },
        )
        self.assertEqual(response.status_code, 302)
        change_order.refresh_from_db()
        self.assertEqual(change_order.price_delta, Decimal('250.00'))
        self.assertEqual(change_order.cost_delta, Decimal('150.00'))
        self.assertEqual(change_order.line_items.count(), 1)
        self.assertEqual(change_order.line_items.get().cost_code, cost_code)

        edit_response = self.client.get(
            reverse('projects:change_order_edit', args=(self.project.pk, change_order.pk))
        )
        self.assertContains(edit_response, 'Computed automatically from line items below.')

        line_item = change_order.line_items.get()
        delete_response = self.client.post(
            reverse(
                'projects:change_order_line_item_delete',
                args=(self.project.pk, change_order.pk, line_item.pk),
            )
        )
        self.assertEqual(delete_response.status_code, 302)
        change_order.refresh_from_db()
        self.assertEqual(change_order.price_delta, Decimal('0'))
        self.assertEqual(change_order.cost_delta, Decimal('0'))

    def test_declined_change_order_can_be_revised_in_place(self):
        change_order = self.create_change_order(status=ChangeOrder.Status.DECLINED)
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse(
                'projects:change_order_revise',
                args=(self.project.pk, change_order.pk),
            )
        )
        self.assertEqual(response.status_code, 302)
        change_order.refresh_from_db()
        self.assertEqual(change_order.status, ChangeOrder.Status.DRAFT)
        self.assertIsNone(change_order.submitted_by)
        self.assertIsNone(change_order.decided_by)
        self.assertTrue(
            ActivityEvent.objects.filter(
                event_type=ActivityEvent.Type.CHANGE_ORDER_REVISED,
            ).exists()
        )

    def test_declined_change_order_can_be_replaced_preserving_chain(self):
        change_order = self.create_change_order(status=ChangeOrder.Status.DECLINED)
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse(
                'projects:change_order_replace',
                args=(self.project.pk, change_order.pk),
            )
        )
        self.assertEqual(response.status_code, 302)
        change_order.refresh_from_db()
        self.assertEqual(change_order.status, ChangeOrder.Status.DECLINED)
        replacement = ChangeOrder.objects.get(replaces=change_order)
        self.assertEqual(replacement.status, ChangeOrder.Status.DRAFT)
        self.assertEqual(replacement.number, change_order.number + 1)
        self.assertEqual(change_order.replaced_by, replacement)

        second_attempt = self.client.post(
            reverse(
                'projects:change_order_replace',
                args=(self.project.pk, change_order.pk),
            )
        )
        self.assertEqual(second_attempt.status_code, 403)

    def test_multi_approval_threshold_requires_distinct_client_votes(self):
        change_order = self.create_change_order(status=ChangeOrder.Status.PENDING)
        change_order.required_approvals = 2
        change_order.save(update_fields=['required_approvals'])
        decision_url = reverse(
            'projects:change_order_decision',
            args=(self.project.pk, change_order.pk),
        )

        self.client.force_login(self.client_user)
        self.client.post(decision_url, {'decision': ChangeOrder.Status.APPROVED})
        change_order.refresh_from_db()
        self.assertEqual(change_order.status, ChangeOrder.Status.PENDING)
        self.assertEqual(change_order.decisions.count(), 1)

        duplicate_vote = self.client.post(
            decision_url, {'decision': ChangeOrder.Status.APPROVED}
        )
        self.assertEqual(duplicate_vote.status_code, 302)
        self.assertEqual(change_order.decisions.count(), 1)

        self.client.force_login(self.second_client)
        self.client.post(decision_url, {'decision': ChangeOrder.Status.APPROVED})
        change_order.refresh_from_db()
        self.assertEqual(change_order.status, ChangeOrder.Status.APPROVED)
        self.assertEqual(change_order.decisions.count(), 2)

    def test_multi_approval_threshold_declined_by_a_single_vote(self):
        change_order = self.create_change_order(status=ChangeOrder.Status.PENDING)
        change_order.required_approvals = 3
        change_order.save(update_fields=['required_approvals'])
        decision_url = reverse(
            'projects:change_order_decision',
            args=(self.project.pk, change_order.pk),
        )

        self.client.force_login(self.client_user)
        self.client.post(decision_url, {'decision': ChangeOrder.Status.DECLINED})
        change_order.refresh_from_db()
        self.assertEqual(change_order.status, ChangeOrder.Status.DECLINED)
