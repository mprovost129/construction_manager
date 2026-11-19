"""Company-scoped selection blueprints; never copy a client's decisions or ledger."""
from contextlib import contextmanager
from pathlib import Path

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from .access import can_manage_project, can_manage_organization
from .models import (ActivityEvent, FinishSelection, Organization, Project,
                     SelectionTemplate, SelectionTemplateRow, SelectionTemplateOption,
                     SelectionOption, SelectionPackage, ChoiceImage)
from .services import record_activity

ROW_FIELDS = ('title', 'description', 'location', 'allowance_amount')
OPTION_FIELDS = ('name', 'description', 'price', 'is_recommended', 'sort_order',
                 'vendor_name', 'product_url', 'specification', 'lead_time', 'image_caption')


def template_organizations(user):
    if getattr(user, '_client_preview_project_id', None):
        return Organization.objects.none()
    if not user.is_authenticated:
        return Organization.objects.none()
    if user.is_superuser:
        return Organization.objects.all()
    memberships = user.organization_memberships.filter(is_active=True).filter(
        Q(role='admin') |
        (Q(project_access__can_manage=True, project_access__is_active=True)
         & ~Q(role__in=('client', 'subcontractor')))
    )
    return Organization.objects.filter(pk__in=memberships.values('organization_id'))


def can_use_templates(user, organization):
    return template_organizations(user).filter(pk=organization.pk).exists()


def audit(template, user, action, project=None, **metadata):
    record_activity(organization=template.organization, project=project, actor=user,
                    event_type=ActivityEvent.Type.SELECTION_UPDATED,
                    summary=f'{user.email} {action} selection template "{template.name}".',
                    metadata={'template_id': template.pk, **metadata})


@contextmanager
def copied_files():
    """Roll back newly copied private objects when any step fails."""
    saved = []
    try:
        with transaction.atomic():
            yield saved
    except Exception:
        for storage, name in saved:
            storage.delete(name)
        raise


def copy_options(source_options, destination, saved):
    for source in source_options:
        model, parent = (SelectionTemplateOption, {'row': destination}) if isinstance(destination, SelectionTemplateRow) else (SelectionOption, {'selection': destination})
        option = model(**parent, **{field: getattr(source, field) for field in OPTION_FIELDS})
        for field in ('image', 'attachment'):
            original = getattr(source, field)
            if original:
                with original.open('rb') as data:
                    copied = getattr(option, field)
                    copied.save(Path(original.name).name, data, save=False)
                    saved.append((copied.storage, copied.name))
        option.full_clean()
        option.save()
        for original_photo in source.gallery_images.all():
            parent = {'template_option': option} if isinstance(option, SelectionTemplateOption) else {'option': option}
            photo = ChoiceImage(**parent, caption=original_photo.caption, sort_order=original_photo.sort_order)
            with original_photo.image.open('rb') as data:
                photo.image.save(Path(original_photo.image.name).name, data, save=False)
                saved.append((photo.image.storage, photo.image.name))
            photo.save()


def save_project_template(project, user, name, description, selections):
    if not can_manage_project(user, project) or not can_use_templates(user, project.organization):
        raise PermissionDenied
    with copied_files() as saved:
        Project.objects.select_for_update().get(pk=project.pk)
        template = SelectionTemplate.objects.create(organization=project.organization, name=name,
                                                    description=description, created_by=user)
        for index, selection in enumerate(selections.select_related('package').prefetch_related('options__gallery_images').order_by('package__title', 'title', 'pk')):
            if selection.project_id != project.pk:
                raise PermissionDenied
            row = SelectionTemplateRow.objects.create(template=template, sort_order=index,
                category=selection.package.title if selection.package_id else '',
                **{field: getattr(selection, field) for field in ROW_FIELDS})
            copy_options(selection.options.all(), row, saved)
        audit(template, user, 'saved', project)
    return template


def copy_template(source, user, name):
    if not can_use_templates(user, source.organization):
        raise PermissionDenied
    with copied_files() as saved:
        source = SelectionTemplate.objects.select_for_update().get(pk=source.pk, deleted_at__isnull=True)
        template = SelectionTemplate.objects.create(organization=source.organization, name=name,
                                                    description=source.description, created_by=user)
        for original in source.rows.prefetch_related('options__gallery_images'):
            row = SelectionTemplateRow.objects.create(template=template, category=original.category,
                sort_order=original.sort_order, **{field: getattr(original, field) for field in ROW_FIELDS})
            copy_options(original.options.all(), row, saved)
        audit(template, user, 'copied', source_template_id=source.pk)
    return template


def row_identity(title, location, category):
    return tuple(value.strip().casefold() for value in (title, location, category))


def load_template(template, project, user, rows, due_date=None):
    if template.organization_id != project.organization_id or not can_manage_project(user, project) or not can_use_templates(user, template.organization):
        raise PermissionDenied
    added = skipped = 0
    with copied_files() as saved:
        Project.objects.select_for_update().get(pk=project.pk)
        template = SelectionTemplate.objects.select_for_update().get(pk=template.pk, deleted_at__isnull=True)
        existing = list(project.finish_selections.select_related('package'))
        keys = {s.template_row_key for s in existing if s.template_row_key}
        identities = {row_identity(s.title, s.location, s.package.title if s.package_id else '') for s in existing}
        number = project.finish_selections.aggregate(value=Max('number'))['value'] or 0
        for row in rows.prefetch_related('options__gallery_images'):
            if row.template_id != template.pk:
                raise PermissionDenied
            identity = row_identity(row.title, row.location, row.category)
            if row.key in keys or identity in identities:
                skipped += 1
                continue
            package = None
            if row.category:
                package = project.selection_packages.filter(title__iexact=row.category).first()
                if not package:
                    package = SelectionPackage.objects.create(project=project, title=row.category, created_by=user)
            number += 1
            selection = FinishSelection.objects.create(project=project, package=package, created_by=user,
                number=number, template_row_key=row.key, due_date=due_date,
                **{field: getattr(row, field) for field in ROW_FIELDS})
            copy_options(row.options.all(), selection, saved)
            identities.add(identity)
            keys.add(row.key)
            added += 1
        audit(template, user, 'loaded', project, added=added, skipped=skipped)
    return added, skipped
