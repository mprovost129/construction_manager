"""Explicit template amendments and reversible replacement of draft selections."""
import hashlib
import json
from collections import defaultdict
from typing import NamedTuple

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .access import can_manage_project
from .allowance_groups import pool_budget, copied_budget
from .models import (
    ActivityEvent,
    ChoiceImage,
    FinishSelection,
    Project,
    SelectionOption,
    SelectionPackage,
    SelectionTemplate,
    SelectionTemplateReplacement,
)
from .selection_templates import (
    OPTION_FIELDS,
    ROW_FIELDS,
    audit,
    can_use_templates,
    contains_export_history,
    copied_files,
    copy_options,
    reusable_description,
    row_identity,
)
from .services import record_activity


class LoadResult(NamedTuple):
    added: int = 0
    skipped: int = 0
    updated: int = 0
    choices_added: int = 0
    protected: int = 0
    ambiguous: int = 0
    backup_id: int | None = None


def choice_name(name):
    return name.strip().casefold()


def row_state(selection):
    fields = {f: str(getattr(selection, f)) for f in ROW_FIELDS}
    fields.update(package_id=selection.package_id, template_row_key=str(selection.template_row_key) if selection.template_row_key else None)
    options = []
    for option in selection.options.order_by('pk').prefetch_related('gallery_images'):
        data = {f: getattr(option, f) for f in OPTION_FIELDS}
        data.update(id=option.pk, price=str(option.price), cost=str(option.cost),
                    image=option.image.name or '', attachment=option.attachment.name or '')
        data['gallery'] = [dict(id=p.pk, image=p.image.name, caption=p.caption, sort_order=p.sort_order)
                           for p in option.gallery_images.order_by('pk')]
        options.append(data)
    return dict(id=selection.pk, fields=fields, options=options, status=selection.status,
                section_allowance=str(selection.package.allowance_amount) if selection.package_id else None,
                allowance_groups=selection.package.allowance_groups if selection.package_id else [],
                chosen_option_id=selection.chosen_option_id, updated_at=selection.updated_at.isoformat())


def revision(states):
    return hashlib.sha256(json.dumps(states, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def project_revision(project):
    return revision([row_state(s) for s in project.finish_selections.order_by('pk')])


def matching_rows(row, by_key, by_identity):
    return by_key.get(row.key) or by_identity.get(row_identity(row.title, row.location, row.category), [])


def apply_template(template, project, user, rows, due_date=None, *, mode='add', expected_revision=None, expected_template_version=None):
    if mode not in ('add', 'amend', 'replace'):
        raise ValidationError('Choose a valid template action.')
    if template.organization_id != project.organization_id or not can_manage_project(user, project) or not can_use_templates(user, template.organization):
        raise PermissionDenied
    added = skipped = updated = choices_added = protected = ambiguous = 0
    before = []
    with copied_files() as saved:
        Project.objects.select_for_update().get(pk=project.pk)
        template = SelectionTemplate.objects.select_for_update().get(pk=template.pk, deleted_at__isnull=True)
        # Lock selections themselves: publishing/editing choices uses the same lock.
        list(project.finish_selections.select_for_update().order_by('pk'))
        if expected_template_version and template.updated_at.isoformat() != expected_template_version:
            raise ValidationError('The saved template changed. Reload this page and review it again.')
        if mode == 'replace' and (not expected_revision or project_revision(project) != expected_revision):
            raise ValidationError('Project selections changed. Reload this page and review before replacing them.')
        existing = list(project.finish_selections.select_related('package').prefetch_related('options'))
        by_key, by_identity = defaultdict(list), defaultdict(list)
        for selection in existing:
            if selection.template_row_key:
                by_key[selection.template_row_key].append(selection)
            by_identity[row_identity(selection.title, selection.location, selection.package.title if selection.package_id else '')].append(selection)
        number = FinishSelection.all_objects.filter(project=project).aggregate(value=Max('number'))['value'] or 0
        touched = set()
        for row in rows.prefetch_related('options__gallery_images'):
            if row.template_id != template.pk:
                raise PermissionDenied
            matches = matching_rows(row, by_key, by_identity)
            if len(matches) > 1 or (matches and matches[0].pk in touched):
                skipped += 1
                ambiguous += 1
                continue
            if matches:
                selection = matches[0]
                touched.add(selection.pk)
                if mode == 'add':
                    skipped += 1
                    continue
                if selection.status != FinishSelection.Status.DRAFT or selection.chosen_option_id:
                    skipped += 1
                    protected += 1
                    continue
                if mode == 'amend':
                    names = {choice_name(o.name) for o in selection.options.all()}
                    missing = [o for o in row.options.all() if choice_name(o.name) not in names]
                    clean_notes = contains_export_history(selection.description)
                    if clean_notes:
                        before.append(row_state(selection))
                        selection.description = reusable_description(row.description, row.title)
                        selection.save(update_fields=['description', 'updated_at'])
                    if missing:
                        copy_options(missing, selection, saved)
                        choices_added += len(missing)
                    if missing or clean_notes:
                        updated += 1
                    else:
                        skipped += 1
                    continue
                before.append(row_state(selection))
                # Old files stay private and are referenced by the undo record.
                selection.options.all().delete()
                for field in ROW_FIELDS:
                    setattr(selection, field, getattr(row, field))
                updated += 1
            else:
                number += 1
                selection = FinishSelection(project=project, number=number, created_by=user,
                    due_date=due_date, **{field: getattr(row, field) for field in ROW_FIELDS})
                added += 1
            package = None
            if row.category:
                package = project.selection_packages.filter(title__iexact=row.category).first()
                if not package:
                    section = template.allowance_sections.filter(category__iexact=row.category).first()
                    package = SelectionPackage.objects.create(project=project, title=row.category, created_by=user,
                        **copied_budget(section, rows.filter(category__iexact=row.category, allowance_mode='section').values_list('allowance_group', flat=True)))
            selection.package = package
            if selection.allowance_mode == 'section' and pool_budget(selection) is None:
                raise ValidationError(f'Enable a shared allowance for {row.category or "this category"} in Manage allowances before loading pooled rows. Existing section budgets are preserved.')
            selection.package = package
            selection.template_row_key = row.key
            selection.description = reusable_description(row.description, row.title)
            selection.save()
            copy_options(row.options.all(), selection, saved)
            choices_added += row.options.count()
            if not matches:
                by_key[row.key].append(selection)
                by_identity[row_identity(row.title, row.location, row.category)].append(selection)
                touched.add(selection.pk)
        backup = None
        if before:
            after = [row_state(project.finish_selections.get(pk=s['id'])) for s in before]
            backup = SelectionTemplateReplacement.objects.create(project=project, template_name=template.name,
                created_by=user, before=before, after_revision=revision(after))
        audit(template, user, 'loaded', project, mode=mode, added=added, skipped=skipped,
              updated=updated, choices_added=choices_added, protected=protected, ambiguous=ambiguous,
              backup_id=backup.pk if backup else None)
    return LoadResult(added, skipped, updated, choices_added, protected, ambiguous, backup.pk if backup else None)


def clean_legacy_project_notes(project, user):
    """Remove report-history blocks from unreleased copies, with an undo record."""
    if not can_manage_project(user, project) or not can_use_templates(user, project.organization):
        raise PermissionDenied
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=project.pk)
        selections = [s for s in project.finish_selections.select_for_update().filter(status='draft', import_source={}).order_by('pk')
                      if contains_export_history(s.description)]
        if not selections:
            return 0
        before = [row_state(s) for s in selections]
        for selection in selections:
            selection.description = reusable_description(selection.description, selection.title)
            selection.save(update_fields=['description', 'updated_at'])
        backup = SelectionTemplateReplacement.objects.create(project=project, created_by=user,
            template_name='Historical approval-note cleanup', before=before,
            after_revision=revision([row_state(s) for s in selections]))
        record_activity(organization=project.organization, project=project, actor=user,
            event_type=ActivityEvent.Type.SELECTION_UPDATED, summary='Removed historical approval notes from unreleased copies.',
            metadata={'replacement_id': backup.pk, 'rows_updated': len(selections)})
    return len(selections)


def undo_replacement(backup, user):
    if not can_manage_project(user, backup.project) or not can_use_templates(user, backup.project.organization):
        raise PermissionDenied
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=backup.project_id)
        backup = SelectionTemplateReplacement.objects.select_for_update().get(pk=backup.pk)
        if backup.restored_at:
            raise ValidationError('This replacement has already been undone.')
        ids = [s['id'] for s in backup.before]
        selections = {s.pk: s for s in backup.project.finish_selections.select_for_update().filter(pk__in=ids)}
        if len(selections) != len(ids) or any(s.status != 'draft' or s.chosen_option_id for s in selections.values()):
            raise ValidationError('Undo is unavailable because a replaced selection was removed or released.')
        current = [row_state(selections[pk]) for pk in ids]
        # Keep pre-allowance-update undo records usable without ignoring a new mode.
        for state, old in zip(current, backup.before):
            if 'allowance_mode' not in old['fields']:
                if state['fields']['allowance_mode'] != 'individual':
                    raise ValidationError('Allowance settings changed after replacement. Undo would overwrite newer work.')
                state['fields'].pop('allowance_mode')
            if 'allowance_group' not in old['fields']:
                if state['fields']['allowance_group']:
                    raise ValidationError('Allowance groups changed after replacement.')
                state['fields'].pop('allowance_group')
            if 'allowance_groups' not in old:
                if state['allowance_groups']:
                    raise ValidationError('Allowance groups changed after replacement.')
                state.pop('allowance_groups')
            if 'section_allowance' not in old:
                state.pop('section_allowance')
        if revision(current) != backup.after_revision:
            raise ValidationError('These selections were edited after replacement. Undo would overwrite newer work, so no changes were made.')
        storage = SelectionOption._meta.get_field('image').storage
        for state in backup.before:
            for data in state['options']:
                paths = [data['image'], data['attachment'], *[p['image'] for p in data['gallery']]]
                if any(path and not storage.exists(path) for path in paths):
                    raise ValidationError('An original file is unavailable. No changes were made; contact an administrator.')
        for state in backup.before:
            selection = selections[state['id']]
            selection.options.all().delete()
            for field, value in state['fields'].items():
                setattr(selection, field, value)
            selection.save()
            for data in state['options']:
                option = SelectionOption.objects.create(selection=selection,
                    **{k: v for k, v in data.items() if k not in ('id', 'gallery')})
                for photo in data['gallery']:
                    ChoiceImage.objects.create(option=option, **{k: v for k, v in photo.items() if k != 'id'})
        backup.restored_at = timezone.now()
        backup.save(update_fields=['restored_at'])
        record_activity(organization=backup.project.organization, project=backup.project, actor=user,
            event_type=ActivityEvent.Type.SELECTION_UPDATED, summary='Undid a template replacement.',
            metadata={'replacement_id': backup.pk, 'rows_restored': len(ids)})
    return len(ids)
