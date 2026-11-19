from collections import OrderedDict
from django.db.models import Q
from .access import can_manage_project, is_project_client
from .models import FinishSelection
from .selection_templates import can_use_templates


def board_context(project, user, params):
    selections = project.finish_selections.select_related('chosen_option', 'package').prefetch_related('options__gallery_images', 'reference_photos')
    if is_project_client(user, project):
        selections = selections.exclude(status=FinishSelection.Status.DRAFT)
    query = params.get('q', '').strip()[:200]
    status = params.get('status', '')
    group = params.get('group', 'category')
    group = group if group in ('category', 'location', 'none') else 'category'
    order = params.get('sort', 'title')
    order = order if order in ('title', '-title', 'due_date', 'status', 'number') else 'title'
    if query:
        selections = selections.filter(Q(title__icontains=query) | Q(location__icontains=query) | Q(package__title__icontains=query))
    if status in FinishSelection.Status.values:
        selections = selections.filter(status=status)
    elif status == 'overdue':
        from django.utils import timezone
        selections = selections.filter(status='open', due_date__lt=timezone.localdate())
    else:
        status = ''
    selections = list(selections.order_by(*((['package__title'] if group == 'category' else ['location'] if group == 'location' else []) + [order, 'pk'])))
    groups, packages, standalone = OrderedDict(), {}, []
    for selection in selections:
        options = list(selection.options.all())
        selection.board_options = options
        selection.board_photos = [photo for photo in selection.reference_photos.all()
                                  if photo.client_visible or can_manage_project(user, project)]
        selection.board_price = selection.imported_total if selection.status == 'imported' else selection.chosen_option.price if selection.chosen_option_id else None
        selection.board_remaining = selection.allowance_amount - selection.board_price if selection.board_price is not None else None
        selection.board_files = sum(bool(o.image) + bool(o.attachment) + len(o.gallery_images.all()) for o in options) + len(selection.board_photos)
        selection.board_image = next((o for o in options if o.image), None)
        label = ((selection.package.title if selection.package_id else 'Uncategorized') if group == 'category'
                 else (selection.location or 'No location') if group == 'location' else 'All selections')
        groups.setdefault(label, []).append(selection)
        if selection.package_id:
            packages.setdefault(selection.package, []).append(selection)
        else:
            standalone.append(selection)
    return {'project': project, 'selections': selections, 'board_groups': list(groups.items()),
            'package_groups': list(packages.items()), 'standalone_selections': standalone,
            'can_manage_project': can_manage_project(user, project),
            'can_use_templates': can_manage_project(user, project) and can_use_templates(user, project.organization),
            'query': query, 'status_filter': status, 'group_filter': group, 'sort_filter': order,
            'status_choices': [(key, label) for key, label in FinishSelection.Status.choices if key != 'draft' or not is_project_client(user, project)],
            'view_mode': 'cards' if params.get('view') == 'cards' else 'list'}
