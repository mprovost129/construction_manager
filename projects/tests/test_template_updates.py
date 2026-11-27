import io
import json
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from projects.models import (
    ChoiceImage,
    FinishSelection,
    SelectionOption,
    SelectionTemplateOption,
    SelectionTemplateReplacement,
)
from projects.selection_template_updates import (
    apply_template,
    clean_legacy_project_notes,
    project_revision,
    undo_replacement,
)
from projects.selection_templates import load_template, save_project_template
from projects.tests.test_selection_templates import SelectionTemplateTests


class TemplateUpdateTests(TestCase):
    setUp = SelectionTemplateTests.setUp
    make_template = SelectionTemplateTests.make_template
    url = SelectionTemplateTests.url

    def prepare(self):
        template = self.make_template()
        load_template(template, self.target, self.admin, template.rows.all())
        return template, self.target.finish_selections.get()

    def replace(self, template, **kwargs):
        return apply_template(template, self.target, self.admin, template.rows.all(), mode='replace',
            expected_revision=project_revision(self.target), expected_template_version=template.updated_at.isoformat(), **kwargs)

    def test_amend_fills_existing_empty_rows_and_is_idempotent(self):
        template, selection = self.prepare()
        selection.options.all().delete()
        selection.title = 'Custom job title'
        selection.save()
        result = apply_template(template, self.target, self.admin, template.rows.all(), mode='amend')
        self.assertEqual((result.added, result.updated, result.choices_added), (0, 1, 1))
        self.assertEqual(self.target.finish_selections.count(), 1)
        selection.refresh_from_db()
        self.assertEqual(selection.title, 'Custom job title')
        with selection.options.get().image.open('rb') as file:
            self.assertEqual(file.read(), b'original-image')
        self.assertEqual(apply_template(template, self.target, self.admin, template.rows.all(), mode='amend').choices_added, 0)

    def test_amend_preserves_existing_price_cost_choices_and_adds_only_missing(self):
        template, selection = self.prepare()
        option = selection.options.get()
        option.name = 'OAK'
        option.price = 900
        option.cost = 345
        option.save()
        SelectionTemplateOption.objects.create(row=template.rows.get(), name='Credit', price=-50)
        result = apply_template(template, self.target, self.admin, template.rows.all(), mode='amend')
        self.assertEqual(result.choices_added, 1)
        option.refresh_from_db()
        self.assertEqual((option.price, option.cost), (900, 345))
        self.assertEqual(selection.options.get(name='Credit').price, -50)

    def test_identity_match_works_for_old_project_rows_without_keys(self):
        template, selection = self.prepare()
        selection.template_row_key = None
        selection.title = selection.title.upper()
        selection.save()
        selection.options.all().delete()
        result = apply_template(template, self.target, self.admin, template.rows.all(), mode='amend')
        self.assertEqual((result.added, result.updated, result.choices_added), (0, 1, 1))

    def test_ambiguous_name_match_is_skipped_but_key_match_wins(self):
        template, selection = self.prepare()
        selection.options.all().delete()
        duplicate = FinishSelection.objects.create(project=self.target, number=2, title=selection.title,
            location=selection.location, package=selection.package, created_by=self.admin)
        self.assertEqual(apply_template(template, self.target, self.admin, template.rows.all(), mode='amend').updated, 1)
        self.assertEqual(duplicate.options.count(), 0)
        selection.template_row_key = None
        selection.save()
        self.assertEqual(apply_template(template, self.target, self.admin, template.rows.all(), mode='amend').ambiguous, 1)

    def test_released_selected_imported_and_voided_rows_are_protected(self):
        template, selection = self.prepare()
        for status in ('open', 'selected', 'imported', 'voided'):
            selection.status = status
            selection.opened_by = self.admin if status != 'imported' else None
            selection.opened_at = timezone.now() if status != 'imported' else None
            selection.selected_by = self.customer if status == 'selected' else None
            selection.selected_at = timezone.now() if status == 'selected' else None
            selection.chosen_option = selection.options.first() if status == 'selected' else None
            selection.voided_by = self.admin if status == 'voided' else None
            selection.voided_at = timezone.now() if status == 'voided' else None
            selection.import_source = {'source_key': 'test'} if status == 'imported' else {}
            selection.imported_total = 100 if status == 'imported' else None
            selection.save()
            for mode in ('amend', 'replace'):
                result = apply_template(template, self.target, self.admin, template.rows.all(), mode=mode,
                    expected_revision=project_revision(self.target))
                self.assertEqual((result.protected, result.updated), (1, 0))
                self.assertEqual(selection.options.count(), 1)

    def test_replacement_and_undo_restore_original_details_costs_and_files(self):
        template, selection = self.prepare()
        option = selection.options.get()
        option.price, option.cost = 900, 345
        option.save()
        # Gallery owns an existing private file, independently tracked in snapshot.
        ChoiceImage.objects.create(option=option, image=option.image.name, caption='Original accessory', sort_order=2)
        SelectionOption.objects.create(selection=selection, name='Project-only choice', price=800, cost=200)
        selection.description = 'Job-specific notes'
        selection.save()
        original_file = option.image.name
        result = self.replace(template)
        self.assertEqual((result.added, result.updated, result.choices_added), (0, 1, 1))
        self.assertEqual(selection.options.count(), 1)
        self.assertEqual(selection.options.get().cost, 0)
        selection.refresh_from_db()
        self.assertNotEqual(selection.description, 'Job-specific notes')
        backup = SelectionTemplateReplacement.objects.get(pk=result.backup_id)
        self.assertEqual(undo_replacement(backup, self.admin), 1)
        selection.refresh_from_db()
        self.assertEqual(selection.description, 'Job-specific notes')
        restored = selection.options.get(name='Oak')
        self.assertEqual((restored.price, restored.cost), (Decimal(900), Decimal(345)))
        self.assertEqual(restored.image.name, original_file)
        self.assertEqual(restored.gallery_images.get().caption, 'Original accessory')
        self.assertEqual(selection.options.count(), 2)
        with restored.image.open('rb') as image:
            self.assertEqual(image.read(), b'original-image')
        with self.assertRaises(ValidationError):
            undo_replacement(backup, self.admin)

    def test_replace_rejects_stale_project_or_template_before_changes(self):
        template, selection = self.prepare()
        for revision, version in (('old', template.updated_at.isoformat()), (project_revision(self.target), 'old')):
            with self.assertRaises(ValidationError):
                apply_template(template, self.target, self.admin, template.rows.all(), mode='replace',
                    expected_revision=revision, expected_template_version=version)
        self.assertFalse(SelectionTemplateReplacement.objects.exists())
        self.assertEqual(selection.options.count(), 1)

    def test_undo_protects_newer_edits_and_published_rows(self):
        template, selection = self.prepare()
        result = self.replace(template)
        backup = SelectionTemplateReplacement.objects.get(pk=result.backup_id)
        option = selection.options.get()
        option.price = 1234
        option.save()
        with self.assertRaises(ValidationError):
            undo_replacement(backup, self.admin)
        self.assertEqual(selection.options.get().price, 1234)
        selection.status = 'open'
        selection.opened_by = self.admin
        selection.opened_at = timezone.now()
        selection.save()
        with self.assertRaises(ValidationError):
            undo_replacement(backup, self.admin)

    def test_file_failure_rolls_back_amend_and_replace(self):
        template, selection = self.prepare()
        original = selection.options.get().pk
        for mode in ('amend', 'replace'):
            SelectionTemplateOption.objects.get_or_create(row=template.rows.get(), name='New option')
            with patch('projects.selection_templates.SelectionOption.full_clean', side_effect=ValueError('failure')):
                with self.assertRaises(ValueError):
                    apply_template(template, self.target, self.admin, template.rows.all(), mode=mode,
                        expected_revision=project_revision(self.target))
            self.assertTrue(selection.options.filter(pk=original).exists())
            self.assertEqual(selection.options.count(), 1)
            self.assertFalse(SelectionTemplateReplacement.objects.exists())

    def test_confirmation_and_private_history_permissions(self):
        template, selection = self.prepare()
        load_url = self.url('load', self.target.pk, template.pk)
        page = self.client.get(load_url)
        self.assertContains(page, 'Amend')
        self.assertContains(page, '(1 choices)')
        payload = {'rows': [template.rows.get().pk], 'mode': 'replace',
            'project_version': project_revision(self.target), 'template_version': template.updated_at.isoformat()}
        self.assertContains(self.client.post(load_url, payload), 'Confirm replacement')
        payload['confirm_replace'] = 'on'
        self.assertEqual(self.client.post(load_url, payload).status_code, 302)
        backup = SelectionTemplateReplacement.objects.get()
        history_url = self.url('replacements', self.target.pk)
        self.assertContains(self.client.get(history_url), 'Undo replacement')
        for user in (self.customer, self.outsider):
            self.client.force_login(user)
            self.assertIn(self.client.get(history_url).status_code, (403, 404))
            with self.assertRaises(PermissionDenied):
                undo_replacement(backup, user)

    def test_amend_cannot_cross_company_or_copy_forged_rows(self):
        template, selection = self.prepare()
        with self.assertRaises(PermissionDenied):
            apply_template(template, self.foreign, self.admin, template.rows.all(), mode='amend')
        other = save_project_template(self.project, self.admin, 'Other', '', self.project.finish_selections.all())
        with self.assertRaises(PermissionDenied):
            apply_template(template, self.target, self.admin, other.rows.all(), mode='amend')

    def test_saving_project_still_creates_independent_new_template_with_choices(self):
        template, selection = self.prepare()
        saved = save_project_template(self.target, self.admin, 'New project template', '', self.target.finish_selections.all())
        self.assertNotEqual(saved.pk, template.pk)
        self.assertEqual(saved.rows.get().options.count(), 1)
        self.assertNotEqual(saved.rows.get().options.get().image.name, selection.options.get().image.name)

    def history_description(self, title):
        return title + '\nUse approved blueprints.\nSelection Total: $100 Remaining from Allowance: $0\nChoices\nChoice Description Status/Alerts Price\nSelected on June 1 By Previous Client'

    def test_imported_approvals_and_ocr_history_do_not_transfer_to_new_project(self):
        self.selection.status = 'imported'
        self.selection.import_source = {'source_key': 'old-job'}
        self.selection.imported_total = 100
        self.selection.description = self.history_description(self.selection.title)
        self.selection.save()
        template = self.make_template()
        self.assertEqual(template.rows.get().description, 'Use approved blueprints.')
        apply_template(template, self.target, self.admin, template.rows.all(), mode='amend')
        loaded = self.target.finish_selections.get()
        self.assertEqual(loaded.status, 'draft')
        self.assertEqual(loaded.buildertrend_status_label, 'Unreleased')
        self.assertEqual(loaded.description, 'Use approved blueprints.')
        self.assertIsNone(loaded.chosen_option_id)
        self.assertIsNone(loaded.selected_by_id)
        self.assertIsNone(loaded.selected_at)
        self.assertIsNone(loaded.opened_at)
        self.assertEqual(loaded.import_source, {})
        self.assertIsNone(loaded.imported_total)
        self.selection.refresh_from_db()
        self.assertIn('Previous Client', self.selection.description)

    def test_amend_cleans_old_approval_notes_without_erasing_normal_instructions(self):
        template, selection = self.prepare()
        row = template.rows.get()
        row.description = 'Approved blueprints are required before installation.'
        row.save()
        selection.description = self.history_description(selection.title)
        selection.save()
        result = apply_template(template, self.target, self.admin, template.rows.all(), mode='amend')
        self.assertEqual(result.updated, 1)
        selection.refresh_from_db()
        self.assertEqual(selection.description, row.description)
        self.assertEqual(selection.status, 'draft')
        self.assertIsNotNone(result.backup_id)

    def test_legacy_note_cleanup_is_reversible_and_only_touches_draft_copies(self):
        template, selection = self.prepare()
        selection.description = self.history_description(selection.title)
        selection.save()
        self.selection.description = self.history_description(self.selection.title)
        self.selection.status = 'imported'
        self.selection.import_source = {'source_key': 'old'}
        self.selection.imported_total = 100
        self.selection.save()
        self.assertEqual(clean_legacy_project_notes(self.target, self.admin), 1)
        self.assertEqual(clean_legacy_project_notes(self.project, self.admin), 0)
        selection.refresh_from_db()
        self.assertEqual(selection.description, 'Use approved blueprints.')
        self.assertEqual(selection.options.count(), 1)
        undo_replacement(SelectionTemplateReplacement.objects.get(), self.admin)
        selection.refresh_from_db()
        self.assertIn('Previous Client', selection.description)

    def test_maintenance_repair_is_dry_run_guarded_and_adds_no_rows(self):
        template, selection = self.prepare()
        selection.options.all().delete()
        selection.description = self.history_description(selection.title)
        selection.save()
        args = dict(project=self.target.pk, template=template.pk, actor=self.admin.email)
        output = io.StringIO()
        call_command('repair_selection_template', **args, stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report['missing_choices'], 1)
        self.assertFalse(report['applied'])
        self.assertFalse(selection.options.exists())
        with self.assertRaises(CommandError):
            call_command('repair_selection_template', **args, apply=True, expected_revision='wrong', stdout=io.StringIO())
        call_command('repair_selection_template', **args, apply=True, expected_revision=report['revision'], stdout=io.StringIO())
        self.assertEqual(self.target.finish_selections.count(), 1)
        self.assertEqual(selection.options.count(), 1)
        selection.refresh_from_db()
        self.assertNotIn('Previous Client', selection.description)
