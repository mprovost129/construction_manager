import copy
import json
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from billing.services import create_invoice_from_change_order
from projects.financials import project_financial_summary
from projects.models import (
    ActivityEvent,
    ChangeOrder,
    FinishSelection,
    Organization,
    OrganizationMembership,
    Project,
)


def sample_payload():
    return {
        'schema_version': 1, 'send_invitations': False, 'send_notifications': False,
        'create_invoices_or_payments': False, 'sources': [],
        'projects': [{
            'staging_key': 'example', 'name': 'Example home', 'proposed_code': 'EX-1',
            'status': 'active', 'contract_amount': None, 'source_jobsite_row': 2,
            'site_address': {'street': 'Example Street'},
            'client': {'name': 'Example Client', 'email': 'client@example.com'},
            'project_managers': [{'name': 'Example Manager'}],
            'change_orders': [{
                'staging_key': 'example:co:1', 'number': 1, 'title': 'Historical order',
                'description': None, 'total_price': '120.00', 'builder_cost': '80.00',
                'status': 'approved', 'approval_actor_verified': False,
                'source_file': 'orders.xlsx', 'source_row': 2,
            }],
            'selections': [{
                'staging_key': 'example:selection:1', 'source_ordinal': 1,
                'title': 'Flooring allowance', 'location_ocr': 'Interior',
                'category_ocr': '10 - Flooring', 'source_selection_total': '-50.25',
                'source_status': 'Selected', 'selected_markers_in_ocr': 1,
                'source_pdf': 'selections.pdf', 'source_page_start': 1, 'source_page_end': 2,
                'source_text_ocr': 'Historical specification, verify original PDF.',
            }],
            'reconciliation': {
                'approved_change_order_count': 1, 'approved_change_order_price': '120.00',
                'approved_change_order_cost': '80.00', 'selection_count': 1,
                'source_printed_selection_net': '-50.25',
            },
        }],
    }


class BuildertrendHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Example', slug='example')
        cls.admin = get_user_model().objects.create_user('admin@example.com', 'password')
        OrganizationMembership.objects.create(organization=cls.org, user=cls.admin, role='admin')

    def run_import(self, data=None, apply=True, **kwargs):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'history.json'
            path.write_text(json.dumps(data or sample_payload()))
            output = StringIO()
            call_command('import_buildertrend_history', file=str(path), organization='example',
                         actor=kwargs.get('actor', self.admin.email), apply=apply, stdout=output)
            return output.getvalue()

    def test_dry_run_validates_without_persisting(self):
        self.assertIn('Dry run passed', self.run_import(apply=False))
        self.assertFalse(Project.objects.exists())
        self.assertFalse(ActivityEvent.objects.exists())

    def test_import_preserves_history_and_is_idempotent(self):
        with patch('django.core.mail.send_mail') as send_mail:
            self.run_import()
            self.run_import()
        send_mail.assert_not_called()
        self.assertEqual(Project.objects.count(), 1)
        self.assertEqual(ActivityEvent.objects.count(), 1)
        project = Project.objects.get()
        self.assertIsNone(project.contract_amount)
        co = ChangeOrder.objects.get()
        self.assertEqual(co.status, 'imported')
        self.assertIsNone(co.decided_by)
        self.assertIsNone(co.submitted_at)
        selection = FinishSelection.objects.get()
        self.assertEqual(selection.imported_total, Decimal('-50.25'))
        self.assertIsNone(selection.selected_at)
        self.assertIsNone(selection.chosen_option)
        self.assertIsNone(selection.selected_variance)
        summary = project_financial_summary(project, include_costs=True)
        self.assertEqual(summary['approved_change_order_total'], Decimal('120.00'))
        self.assertEqual(summary['approved_change_order_cost_total'], Decimal('80.00'))
        self.assertEqual(summary['selection_credit_total'], Decimal('0.00'))
        self.assertIsNone(summary['total_project_cost'])
        self.assertFalse(project.invoices.exists())

    def test_changed_source_refuses_overwrite(self):
        self.run_import()
        changed = sample_payload()
        changed['projects'][0]['name'] = 'Changed title'
        with self.assertRaisesMessage(CommandError, 'Previously imported source changed'):
            self.run_import(changed)
        self.assertEqual(Project.objects.get().name, 'Example home')

    def test_bad_reconciliation_leaves_no_partial_projects(self):
        data = sample_payload()
        other = copy.deepcopy(data['projects'][0])
        other.update(staging_key='other', proposed_code='EX-2', name='Other home')
        other['reconciliation']['source_printed_selection_net'] = '0.00'
        data['projects'].append(other)
        with self.assertRaises(CommandError):
            self.run_import(data)
        self.assertFalse(Project.objects.exists())

    def test_collision_rolls_back_entire_batch(self):
        Project.objects.create(organization=self.org, name='Other home', code='EX-2')
        data = sample_payload()
        other = copy.deepcopy(data['projects'][0])
        other.update(staging_key='other', proposed_code='EX-2', name='Other home')
        data['projects'].append(other)
        with self.assertRaises(CommandError):
            self.run_import(data)
        self.assertEqual(Project.objects.count(), 1)
        self.assertFalse(ChangeOrder.objects.exists())

    def test_non_admin_actor_is_rejected(self):
        OrganizationMembership.objects.filter(user=self.admin).update(role='staff')
        with self.assertRaises(CommandError):
            self.run_import()
        self.assertFalse(Project.objects.exists())

    def test_native_approval_still_requires_authenticated_decision(self):
        self.run_import()
        co = ChangeOrder.objects.get()
        co.status = ChangeOrder.Status.APPROVED
        with self.assertRaises(ValidationError):
            co.full_clean()
        selection = FinishSelection.objects.get()
        selection.status = FinishSelection.Status.SELECTED
        with self.assertRaises(ValidationError):
            selection.full_clean()

    def test_imported_records_are_readonly_and_not_billable(self):
        self.run_import()
        self.client.force_login(self.admin)
        project, co, selection = Project.objects.get(), ChangeOrder.objects.get(), FinishSelection.objects.get()
        for name, record in [('change_order_detail', co), ('selection_detail', selection)]:
            response = self.client.get(reverse(f'projects:{name}', args=[project.pk, record.pk]))
            self.assertContains(response, 'Imported history')
            self.assertContains(response, 'Source record')
            self.assertNotContains(response, 'Save changes')
        for name, record in [('change_order_edit', co), ('selection_edit', selection),
                             ('change_order_void', co), ('selection_reopen', selection)]:
            response = self.client.post(reverse(f'projects:{name}', args=[project.pk, record.pk]), {'title': 'Bad edit'})
            self.assertIn(response.status_code, (302, 400, 403, 404))
        co.refresh_from_db()
        selection.refresh_from_db()
        self.assertEqual(co.status, 'imported')
        self.assertEqual(selection.status, 'imported')
        with self.assertRaises(ValidationError):
            create_invoice_from_change_order(change_order_id=co.pk, actor=self.admin, form_data={})

    def test_same_source_key_in_other_company_does_not_adopt_project(self):
        other = Organization.objects.create(name='Other', slug='other')
        Project.objects.create(organization=other, name='Example home', code='EX-1',
                               import_source={'source_key': 'example'})
        self.run_import()
        self.assertEqual(Project.objects.count(), 2)
        self.assertEqual(ChangeOrder.objects.get().project.organization, self.org)

    def test_database_rejects_import_without_provenance(self):
        from django.db import IntegrityError, transaction
        self.run_import()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ChangeOrder.objects.update(import_source={})
        with self.assertRaises(IntegrityError), transaction.atomic():
            FinishSelection.objects.update(imported_total=None)
