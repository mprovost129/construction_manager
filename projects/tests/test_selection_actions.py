from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from projects.models import FinishSelection, SelectionOption
from projects.selection_actions import archive_rows
from projects.selection_template_updates import apply_template
from projects.selection_templates import load_template
from projects.tests.test_selection_templates import SelectionTemplateTests


class SelectionActionTests(TestCase):
    setUp = SelectionTemplateTests.setUp
    make_template = SelectionTemplateTests.make_template

    def url(self, name, *args):
        return reverse('projects:selection_' + name, args=args)

    def test_single_delete_is_recoverable_and_hides_all_normal_routes(self):
        original = self.option.image.name
        response = self.client.post(self.url('delete', self.project.pk, self.selection.pk), {'confirm': 'on'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.project.finish_selections.exists())
        self.assertFalse(FinishSelection.objects.filter(pk=self.selection.pk).exists())
        self.assertTrue(FinishSelection.all_objects.get(pk=self.selection.pk).archived_at)
        self.assertTrue(SelectionOption.objects.filter(pk=self.option.pk).exists())
        self.assertEqual(self.client.get(self.url('detail', self.project.pk, self.selection.pk)).status_code, 404)
        self.assertNotContains(self.client.get(self.url('list', self.project.pk)), self.selection.title)
        self.assertContains(self.client.get(self.url('deleted', self.project.pk)), self.selection.title)
        self.client.post(self.url('deleted', self.project.pk), {'selection': self.selection.pk})
        self.assertTrue(self.project.finish_selections.exists())
        restored = self.project.finish_selections.get().options.get()
        self.assertEqual(restored.cost, self.option.cost)
        self.assertEqual(restored.image.name, original)

    def test_delete_requires_confirmation_and_blocks_released_selections(self):
        self.assertEqual(self.client.post(self.url('delete', self.project.pk, self.selection.pk), {}).status_code, 200)
        self.assertTrue(self.project.finish_selections.exists())
        self.selection.status = 'open'
        self.selection.opened_by = self.admin
        self.selection.opened_at = timezone.now()
        self.selection.save()
        self.assertEqual(self.client.post(self.url('delete', self.project.pk, self.selection.pk), {'confirm': 'on'}).status_code, 404)
        with self.assertRaises(ValidationError):
            archive_rows(self.project, self.admin, [self.selection.pk])

    def test_bulk_template_delete_keeps_saved_template_and_manual_rows(self):
        template = self.make_template()
        load_template(template, self.target, self.admin, template.rows.all())
        loaded = self.target.finish_selections.get()
        manual = FinishSelection.objects.create(project=self.target, number=2, title='Manual row', created_by=self.admin)
        url = self.url('delete_template', self.target.pk)
        self.assertContains(self.client.get(url), loaded.title)
        self.assertNotContains(self.client.get(url), 'Manual row')
        self.assertEqual(self.client.post(url, {'selections': [loaded.pk], 'confirm': 'on'}).status_code, 302)
        self.assertEqual(list(self.target.finish_selections.values_list('pk', flat=True)), [manual.pk])
        self.assertEqual(template.rows.count(), 1)
        self.assertEqual(template.rows.get().options.count(), 1)

    def test_copy_carries_choices_files_but_no_approval_or_costs(self):
        self.selection.status = 'selected'
        self.selection.opened_by = self.admin
        self.selection.opened_at = timezone.now()
        self.selection.selected_by = self.customer
        self.selection.selected_at = timezone.now()
        self.selection.chosen_option = self.option
        self.selection.save()
        self.assertEqual(self.client.post(self.url('copy', self.project.pk, self.selection.pk), {'title': 'New copied selection'}).status_code, 302)
        copied = self.project.finish_selections.get(title='New copied selection')
        self.assertEqual(copied.status, 'draft')
        self.assertIsNone(copied.chosen_option_id)
        self.assertIsNone(copied.selected_at)
        self.assertIsNone(copied.template_row_key)
        option = copied.options.get()
        self.assertEqual(option.cost, 0)
        self.assertNotEqual(option.image.name, self.option.image.name)

    def test_deleted_numbers_not_reused_when_copying_or_loading(self):
        template = self.make_template()
        load_template(template, self.target, self.admin, template.rows.all())
        old = self.target.finish_selections.get()
        archive_rows(self.target, self.admin, [old.pk])
        result = apply_template(template, self.target, self.admin, template.rows.all(), mode='amend')
        self.assertEqual(result.added, 1)
        self.assertGreater(self.target.finish_selections.get().number, old.number)
        url = self.url('copy', self.target.pk, self.target.finish_selections.get().pk)
        self.assertEqual(self.client.post(url, {'title': 'Second copy'}).status_code, 302)
        self.assertEqual(FinishSelection.all_objects.filter(project=self.target).count(), 3)

    def test_restore_blocks_active_template_duplicate(self):
        template = self.make_template()
        load_template(template, self.target, self.admin, template.rows.all())
        old = self.target.finish_selections.get()
        archive_rows(self.target, self.admin, [old.pk])
        load_template(template, self.target, self.admin, template.rows.all())
        self.client.post(self.url('deleted', self.target.pk), {'selection': old.pk})
        self.assertEqual(self.target.finish_selections.count(), 1)
        self.assertTrue(FinishSelection.all_objects.get(pk=old.pk).archived_at)

    def test_clients_and_other_company_cannot_manage_or_restore(self):
        for user in (self.customer, self.outsider):
            self.client.force_login(user)
            for name, args in [('copy', (self.project.pk, self.selection.pk)), ('delete', (self.project.pk, self.selection.pk)),
                               ('delete_template', (self.project.pk,)), ('deleted', (self.project.pk,))]:
                self.assertIn(self.client.get(self.url(name, *args)).status_code, (403, 404))
                self.assertIn(self.client.post(self.url(name, *args), {'confirm': 'on', 'selection': self.selection.pk}).status_code, (403, 404))
            with self.assertRaises(PermissionDenied):
                archive_rows(self.project, user, [self.selection.pk])
        self.assertTrue(self.project.finish_selections.exists())

    def test_client_preview_cannot_copy_or_delete(self):
        self.client.post(reverse('projects:client_preview_start'), {'project': self.project.pk})
        for name in ('copy', 'delete'):
            self.assertEqual(self.client.post(self.url(name, self.project.pk, self.selection.pk), {'confirm': 'on', 'title': 'Fake'}).status_code, 403)
