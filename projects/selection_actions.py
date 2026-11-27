"""Project selection copy and recoverable draft deletion."""
from .allowance_groups import pool_budget

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .access import can_manage_project
from .models import ActivityEvent, FinishSelection, Project
from .selection_templates import (
    ROW_FIELDS,
    copied_files,
    copy_options,
    reusable_description,
)
from .services import record_activity
from .views import managed_project_or_404


class ConfirmSelectionForm(forms.Form):
    confirm = forms.BooleanField(label='Delete this unreleased selection from this project. It can be restored from Deleted selections.')


@login_required
@require_http_methods(['GET', 'POST'])
def copy_selection(request, pk, selection_pk):
    project = managed_project_or_404(request.user, pk)
    source = get_object_or_404(project.finish_selections, pk=selection_pk)
    class CopyForm(forms.Form):
        title = forms.CharField(max_length=200, label='New selection title')
    form = CopyForm(request.POST or None, initial={'title': (source.title[:193] + ' — copy')})
    if request.method == 'POST' and form.is_valid():
        with copied_files() as saved:
            Project.objects.select_for_update().get(pk=project.pk)
            source = get_object_or_404(project.finish_selections.select_for_update(), pk=selection_pk)
            number = FinishSelection.all_objects.filter(project=project).aggregate(n=Max('number'))['n'] or 0
            fields = {f: getattr(source, f) for f in ROW_FIELDS}
            fields.update(title=form.cleaned_data['title'], description=reusable_description(source.description, source.title))
            selection = FinishSelection.objects.create(project=project, number=number + 1, created_by=request.user,
                package=source.package, **fields)
            copy_options(source.options.prefetch_related('gallery_images'), selection, saved)
            record_activity(organization=project.organization, project=project, actor=request.user,
                event_type=ActivityEvent.Type.SELECTION_CREATED, summary='Copied a selection as a new unreleased row.',
                metadata={'selection_id': selection.pk, 'source_selection_id': source.pk})
        messages.success(request, 'Selection copied with its choices and photos. Review prices and add internal costs; previous approvals and deadlines were not copied.')
        return redirect('projects:selection_detail', pk=pk, selection_pk=selection.pk)
    return render(request, 'projects/selection_template_form.html', {'form': form, 'title': 'Copy selection',
        'submit_label': 'Copy selection',
        'back_url': reverse('projects:selection_list', args=[pk]),
        'help_text': 'Create an independent unreleased selection in this project, including choices and their photos. Approvals, client comments, old deadlines, and internal costs are not copied.'})


def archive_rows(project, user, ids):
    if not can_manage_project(user, project):
        raise PermissionDenied
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=project.pk)
        rows = list(project.finish_selections.select_for_update().filter(pk__in=ids))
        if len(rows) != len(set(ids)) or any(s.status != 'draft' or s.chosen_option_id for s in rows):
            raise ValidationError('One or more selections changed or were released. Reload and review; nothing was deleted.')
        now = timezone.now()
        for selection in rows:
            selection.archived_at = now
            selection.archived_by = user
            selection.save(update_fields=['archived_at', 'archived_by', 'updated_at'])
        record_activity(organization=project.organization, project=project, actor=user,
            event_type=ActivityEvent.Type.SELECTION_UPDATED, summary=f'Moved {len(rows)} unreleased selections to Deleted selections.',
            metadata={'selection_ids': [s.pk for s in rows], 'recoverable': True})
    return len(rows)


@login_required
@require_http_methods(['GET', 'POST'])
def delete_selection(request, pk, selection_pk):
    project = managed_project_or_404(request.user, pk)
    selection = get_object_or_404(project.finish_selections, pk=selection_pk, status='draft')
    form = ConfirmSelectionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            archive_rows(project, request.user, [selection.pk])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Selection moved to Deleted selections. The shared template is unchanged.')
            return redirect('projects:selection_list', pk=pk)
    return render(request, 'projects/selection_template_form.html', {'form': form, 'title': f'Delete selection: {selection.title}',
        'submit_label': 'Delete selection',
        'back_url': reverse('projects:selection_list', args=[pk]),
        'help_text': f'This removes the selection and its {selection.options.count()} choices from the active project list. Its photos and data are retained for Restore.'})


@login_required
@require_http_methods(['GET', 'POST'])
def delete_project_template(request, pk):
    project = managed_project_or_404(request.user, pk)
    candidates = project.finish_selections.filter(status='draft', template_row_key__isnull=False).order_by('number')
    class DeleteForm(forms.Form):
        selections = forms.ModelMultipleChoiceField(queryset=candidates, widget=forms.CheckboxSelectMultiple,
            label='Loaded template selections to delete')
        confirm = forms.BooleanField(label='Move these unreleased selections to Deleted selections. Do not delete the shared saved template.')
    form = DeleteForm(request.POST or None, initial={'selections': candidates.values_list('pk', flat=True)})
    if request.method == 'POST' and form.is_valid():
        try:
            count = archive_rows(project, request.user, list(form.cleaned_data['selections'].values_list('pk', flat=True)))
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, f'{count} loaded selections moved to Deleted selections. The shared saved template and released selections were kept.')
            return redirect('projects:selection_list', pk=pk)
    return render(request, 'projects/selection_template_form.html', {'form': form, 'title': 'Delete template selections from this project',
        'submit_label': 'Delete selected selections',
        'back_url': reverse('projects:selection_list', args=[pk]),
        'help_text': f'{candidates.count()} unreleased selections loaded from templates are available below. Select all to remove the loaded template, or uncheck rows to keep. Manually added rows and released/selected rows are not included. Everything deleted here can be restored.'})


@login_required
@require_http_methods(['GET', 'POST'])
def deleted_selections(request, pk):
    project = managed_project_or_404(request.user, pk)
    archived = FinishSelection.all_objects.filter(project=project, archived_at__isnull=False)
    if request.method == 'POST':
        selection = get_object_or_404(archived, pk=request.POST.get('selection'))
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project.pk)
            selection = get_object_or_404(archived.select_for_update(), pk=selection.pk)
            match = Q(title__iexact=selection.title, location__iexact=selection.location, package_id=selection.package_id)
            if selection.template_row_key:
                match |= Q(template_row_key=selection.template_row_key)
            if project.finish_selections.filter(match).exists():
                messages.error(request, 'An active matching selection already exists. Review it before restoring to avoid duplicates.')
            elif selection.uses_section_allowance and pool_budget(selection) is None:
                messages.error(request, 'Enable this section allowance in Manage allowances before restoring this selection.')
            else:
                selection.archived_at = None
                selection.archived_by = None
                selection.save(update_fields=['archived_at', 'archived_by', 'updated_at'])
                record_activity(organization=project.organization, project=project, actor=request.user,
                    event_type=ActivityEvent.Type.SELECTION_UPDATED, summary='Restored a deleted selection.',
                    metadata={'selection_id': selection.pk})
                messages.success(request, 'Selection restored with its original choices, photos, and internal costs.')
        return redirect('projects:selection_deleted', pk=pk)
    return render(request, 'projects/selection_deleted.html', {'project': project,
        'page_obj': Paginator(archived.order_by('-archived_at', '-pk'), 50).get_page(request.GET.get('page'))})
