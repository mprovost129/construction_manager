"""Guarded maintenance repair for already-loaded template rows; dry-run by default."""
import json
from collections import defaultdict

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from projects.access import can_manage_project
from projects.models import Project, SelectionTemplate
from projects.selection_template_updates import (
    apply_template,
    choice_name,
    clean_legacy_project_notes,
    matching_rows,
    project_revision,
    revision,
)
from projects.selection_templates import (
    can_use_templates,
    contains_export_history,
    row_identity,
)


class Command(BaseCommand):
    help = 'Preview missing choices and historical notes; --apply requires the exact reviewed revision.'

    def add_arguments(self, parser):
        parser.add_argument('--project', type=int, required=True)
        parser.add_argument('--template', type=int, required=True)
        parser.add_argument('--actor', required=True, help='Authorized staff email; never a password.')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--expected-revision')

    def handle(self, *args, **options):
        with transaction.atomic():
            project = Project.objects.select_for_update().get(pk=options['project'])
            template = SelectionTemplate.objects.select_for_update().get(pk=options['template'], deleted_at__isnull=True)
            user = get_user_model().objects.get(email__iexact=options['actor'], is_active=True)
            if project.organization_id != template.organization_id or not can_manage_project(user, project) or not can_use_templates(user, project.organization):
                raise PermissionDenied
            list(project.finish_selections.select_for_update().order_by('pk'))
            token = revision([project_revision(project), template.pk, template.updated_at.isoformat()])
            by_key, by_identity = defaultdict(list), defaultdict(list)
            selections = list(project.finish_selections.select_related('package').prefetch_related('options'))
            for selection in selections:
                if selection.template_row_key:
                    by_key[selection.template_row_key].append(selection)
                by_identity[row_identity(selection.title, selection.location, selection.package.title if selection.package_id else '')].append(selection)
            row_ids, missing, ambiguous, seen = [], 0, 0, set()
            for row in template.rows.prefetch_related('options'):
                matches = matching_rows(row, by_key, by_identity)
                if len(matches) != 1 or matches[0].pk in seen:
                    ambiguous += bool(matches)
                    continue
                selection = matches[0]
                seen.add(selection.pk)
                if selection.status != 'draft' or selection.chosen_option_id:
                    continue
                row_ids.append(row.pk)
                names = {choice_name(o.name) for o in selection.options.all()}
                missing += sum(choice_name(o.name) not in names for o in row.options.all())
            notes = sum(s.status == 'draft' and not s.import_source and contains_export_history(s.description) for s in selections)
            report = {'project': project.name, 'template': template.name, 'missing_choices': missing,
                      'historical_note_rows': notes, 'ambiguous_rows': ambiguous, 'revision': token, 'applied': False}
            if options['apply']:
                if options['expected_revision'] != token:
                    raise CommandError('The project/template changed or no reviewed revision was supplied. Run the preview again.')
                result = apply_template(template, project, user, template.rows.filter(pk__in=row_ids), mode='amend')
                if result.added or result.choices_added != missing:
                    raise CommandError('The repair counts changed. Nothing was committed.')
                clean_legacy_project_notes(project, user)
                report.update(applied=True, choices_added=result.choices_added,
                              rows_after=project.finish_selections.count(),
                              choices_after=sum(s.options.count() for s in project.finish_selections.all()))
            self.stdout.write(json.dumps(report))
