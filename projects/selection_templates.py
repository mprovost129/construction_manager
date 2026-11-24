"""Company-scoped selection blueprints; never copy a client's decisions or ledger."""
import re
from contextlib import contextmanager
from pathlib import Path

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q

from .access import can_manage_project
from .models import (
    ActivityEvent,
    ChoiceImage,
    Organization,
    Project,
    SelectionOption,
    SelectionTemplate,
    SelectionTemplateOption,
    SelectionTemplateRow,
    SelectionTemplateSection,
)
from .services import record_activity

ROW_FIELDS = ('title', 'description', 'location', 'allowance_amount', 'allowance_mode')
OPTION_FIELDS = ('name', 'description', 'price', 'is_recommended', 'sort_order',
                 'vendor_name', 'product_url', 'specification', 'lead_time', 'image_caption')


def contains_export_history(text):
    """Recognize our raw Buildertrend selection-report blocks, not ordinary prose."""
    return bool(re.search(r'Selection\s+Total\s*:', text, re.I)
                and re.search(r'Remaining\s+from\s+Allowance\s*:', text, re.I)
                and 'Status/Alerts' in text)


def reusable_description(text, title):
    if not contains_export_history(text):
        return text
    prefix = re.split(r'Selection\s+Total\s*:', text, maxsplit=1, flags=re.I)[0].strip()
    lines = prefix.splitlines()
    if lines and lines[0].strip().casefold() == title.strip().casefold():
        lines = lines[1:]
    return '\n'.join(lines).strip()


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


def copy_options(source_options, destination, saved, *, include_files=True):
    for source in source_options:
        model, parent = (SelectionTemplateOption, {'row': destination}) if isinstance(destination, SelectionTemplateRow) else (SelectionOption, {'selection': destination})
        option = model(**parent, **{field: getattr(source, field) for field in OPTION_FIELDS})
        for field in (('image', 'attachment') if include_files else ()):
            original = getattr(source, field)
            if original:
                with original.open('rb') as data:
                    copied = getattr(option, field)
                    copied.save(Path(original.name).name, data, save=False)
                    saved.append((copied.storage, copied.name))
        option.full_clean()
        option.save()
        for original_photo in (source.gallery_images.all() if include_files else ()):
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
                **{field: reusable_description(selection.description, selection.title) if field == 'description'
                   else getattr(selection, field) for field in ROW_FIELDS})
            copy_options(selection.options.all(), row, saved)
        for package in project.selection_packages.filter(pk__in=selections.values_list('package_id', flat=True), allowance_amount__isnull=False):
            SelectionTemplateSection.objects.create(template=template, category=package.title, allowance_amount=package.allowance_amount)
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
        for section in source.allowance_sections.all():
            SelectionTemplateSection.objects.create(template=template, category=section.category, allowance_amount=section.allowance_amount)
        audit(template, user, 'copied', source_template_id=source.pk)
    return template


def row_identity(title, location, category):
    return tuple(value.strip().casefold() for value in (title, location, category))


def load_template(template, project, user, rows, due_date=None):
    """Legacy add-only API; interactive loads use the explicit transfer modes."""
    from .selection_template_updates import apply_template
    result = apply_template(template, project, user, rows, due_date, mode='add')
    return result.added, result.skipped
