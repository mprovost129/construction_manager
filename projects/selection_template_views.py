from pathlib import Path

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.forms import inlineformset_factory
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .choice_images import saved_choice_files
from .forms import SelectionOptionForm
from .access import projects_for_user, can_manage_project
from .models import SelectionTemplate, SelectionTemplateRow, SelectionTemplateOption
from .selection_templates import (template_organizations, save_project_template, copy_template,
                                  load_template, audit)
from .views import managed_project_or_404


class TemplateForm(forms.ModelForm):
    class Meta:
        model = SelectionTemplate
        fields = ('name', 'description')
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}


class RowForm(forms.ModelForm):
    class Meta:
        model = SelectionTemplateRow
        fields = ('category', 'title', 'location', 'allowance_amount', 'sort_order', 'description')
        labels = {'sort_order': 'Order', 'allowance_amount': 'Allowance'}
        help_texts = {
            'allowance_amount': 'For a standalone credit, use a zero allowance and a negative choice price.',
            'sort_order': 'Lower numbers appear first across all categories. Rows with the same number keep their creation order.',
        }
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}


RowsFormSet = inlineformset_factory(SelectionTemplate, SelectionTemplateRow, form=RowForm,
    fields=('category', 'title', 'location', 'allowance_amount', 'sort_order'), extra=1,
    can_delete=True, max_num=100, absolute_max=100, validate_max=True)


class TemplateOptionForm(SelectionOptionForm):
    class Meta(SelectionOptionForm.Meta):
        model = SelectionTemplateOption
        fields = tuple(f for f in SelectionOptionForm.Meta.fields if f != 'cost')
        widgets = {**SelectionOptionForm.Meta.widgets,
                   'image': forms.FileInput(attrs={'accept': 'image/png,image/jpeg,image/webp'}),
                   'attachment': forms.FileInput()}


class SelectionSaveForm(TemplateForm):
    selections = forms.ModelMultipleChoiceField(queryset=None, widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['selections'].queryset = project.finish_selections.order_by('package__title', 'title')
        self.fields['selections'].label_from_instance = lambda row: f'{row.title} — {row.location or "No location"}'


class TemplateLoadForm(forms.Form):
    rows = forms.ModelMultipleChoiceField(queryset=None, widget=forms.CheckboxSelectMultiple)
    due_date = forms.DateField(required=False, label='New deadline (optional)', widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, template, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['rows'].queryset = template.rows.all()
        self.fields['rows'].label_from_instance = lambda row: f'{row.category + " / " if row.category else ""}{row.title} — {row.location or "No location"}'


def organization_for(user, slug):
    return get_object_or_404(template_organizations(user), slug=slug)


def template_for(user, slug, template_pk, *, deleted=False):
    organization = organization_for(user, slug)
    return get_object_or_404(SelectionTemplate, organization=organization, pk=template_pk,
                            deleted_at__isnull=not deleted)


def detail_url(template):
    return reverse('projects:selection_template_detail', args=[template.organization.slug, template.pk])


def editor(request, form, title, back, help_text='', **context):
    return render(request, 'projects/selection_template_form.html',
                  {'form': form, 'title': title, 'back_url': back, 'help_text': help_text, **context})


@login_required
@require_http_methods(['GET'])
def home(request):
    organizations = list(template_organizations(request.user).order_by('name', 'pk'))
    if not organizations:
        raise PermissionDenied
    if len(organizations) == 1:
        return redirect('projects:selection_template_library', slug=organizations[0].slug)
    return render(request, 'projects/selection_template_home.html', {'organizations': organizations})


@login_required
@require_http_methods(['GET'])
def library(request, slug):
    organization = organization_for(request.user, slug)
    templates = organization.selection_templates.filter(deleted_at__isnull=request.GET.get('deleted') != '1').annotate(row_count=Count('rows'))
    query = request.GET.get('q', '').strip()[:200]
    if query:
        templates = templates.filter(Q(name__icontains=query) | Q(description__icontains=query))
    project = None
    if request.GET.get('project'):
        project = managed_project_or_404(request.user, request.GET['project'])
        if project.organization_id != organization.pk:
            raise Http404
    return render(request, 'projects/selection_template_library.html',
                  {'organization': organization, 'templates': templates, 'query': query,
                   'deleted': request.GET.get('deleted') == '1', 'project': project})


@login_required
@require_http_methods(['GET', 'POST'])
def create(request, slug):
    organization = organization_for(request.user, slug)
    form = TemplateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            template = form.save(commit=False)
            template.organization = organization
            template.created_by = request.user
            template.save()
            audit(template, request.user, 'created')
        return redirect(detail_url(template))
    return editor(request, form, 'Create selection template', reverse('projects:selection_template_library', args=[slug]))


@login_required
@require_http_methods(['GET', 'POST'])
def detail(request, slug, template_pk):
    template = template_for(request.user, slug, template_pk)
    conflict = False
    with transaction.atomic():
        if request.method == 'POST':
            template = SelectionTemplate.objects.select_for_update().get(pk=template.pk, deleted_at__isnull=True)
            conflict = request.POST.get('version') != template.updated_at.isoformat()
        page = Paginator(template.rows.all(), 50).get_page(request.GET.get('page'))
        visible_ids = list(page.object_list.values_list('pk', flat=True))
        formset = RowsFormSet(request.POST if request.method == 'POST' else None, instance=template,
                             queryset=template.rows.filter(pk__in=visible_ids), prefix='rows')
        if request.method == 'POST' and not conflict and formset.is_valid():
            # Hidden IDs are untrusted, even though the inline formset scopes its queryset.
            allowed = set(visible_ids)
            if any(f.cleaned_data.get('id') and f.cleaned_data['id'].pk not in allowed for f in formset.forms):
                raise PermissionDenied
            formset.save()
            template.save(update_fields=['updated_at'])
            audit(template, request.user, 'updated rows in')
            messages.success(request, 'Template saved.')
            return redirect(detail_url(template) + f'?page={page.number}')
    if conflict:
        messages.error(request, 'This template changed in another window. Reload it before saving; your changes were not applied.')
    return render(request, 'projects/selection_template_detail.html',
        {'template': template, 'formset': formset, 'version': request.POST.get('version') if conflict else template.updated_at.isoformat(),
         'rows': template.rows.filter(pk__in=visible_ids).prefetch_related('options__gallery_images'), 'conflict': conflict, 'page': page})


@login_required
@require_http_methods(['GET', 'POST'])
def metadata(request, slug, template_pk):
    template = template_for(request.user, slug, template_pk)
    form = TemplateForm(request.POST or None, instance=template)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            form.save()
            audit(template, request.user, 'renamed or described')
        return redirect(detail_url(template))
    return editor(request, form, 'Template name and description', detail_url(template))


@login_required
@require_http_methods(['GET', 'POST'])
def copy(request, slug, template_pk):
    template = template_for(request.user, slug, template_pk)
    class CopyForm(forms.Form):
        name = forms.CharField(max_length=200, label='Name for the copy')
    form = CopyForm(request.POST or None, initial={'name': f'{template.name[:190]} — copy'})
    if request.method == 'POST' and form.is_valid():
        copied = copy_template(template, request.user, form.cleaned_data['name'])
        messages.success(request, 'Independent template copy created, including photos and files.')
        return redirect(detail_url(copied))
    return editor(request, form, 'Copy template', detail_url(template))


@login_required
@require_http_methods(['GET', 'POST'])
def delete(request, slug, template_pk):
    restore = request.GET.get('restore') == '1'
    template = template_for(request.user, slug, template_pk, deleted=restore)
    class ConfirmForm(forms.Form):
        confirm = forms.BooleanField(label='Restore this template' if restore else 'Delete this template from the active library')
    form = ConfirmForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            template = SelectionTemplate.objects.select_for_update().get(pk=template.pk)
            template.deleted_at = None if restore else timezone.now()
            template.save(update_fields=['deleted_at', 'updated_at'])
            audit(template, request.user, 'restored' if restore else 'deleted')
        return redirect('projects:selection_template_library', slug=slug)
    return editor(request, form, ('Restore ' if restore else 'Delete ') + template.name,
                  reverse('projects:selection_template_library', args=[slug]),
                  'Existing project selections and other copies stay unchanged. Deleted templates can be restored from the library.')


@login_required
@require_http_methods(['GET', 'POST'])
def row_edit(request, slug, template_pk, row_pk):
    template = template_for(request.user, slug, template_pk)
    row = get_object_or_404(template.rows, pk=row_pk)
    form = RowForm(request.POST or None, instance=row)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            SelectionTemplate.objects.select_for_update().get(pk=template.pk, deleted_at__isnull=True)
            form.save()
            template.save(update_fields=['updated_at'])
            audit(template, request.user, 'edited a row in')
        return redirect(detail_url(template))
    return editor(request, form, 'Edit template row', detail_url(template))


@login_required
@require_http_methods(['GET', 'POST'])
def option_edit(request, slug, template_pk, row_pk, option_pk=None):
    template = template_for(request.user, slug, template_pk)
    row = get_object_or_404(template.rows, pk=row_pk)
    option = get_object_or_404(row.options, pk=option_pk) if option_pk else SelectionTemplateOption(row=row)
    form = TemplateOptionForm(request.POST or None, request.FILES or None, instance=option)
    if request.method == 'POST' and form.is_valid():
        with saved_choice_files(form), transaction.atomic():
            SelectionTemplate.objects.select_for_update().get(pk=template.pk, deleted_at__isnull=True)
            option = form.save()
            form.save_gallery(option)
            template.save(update_fields=['updated_at'])
            audit(template, request.user, 'saved a choice in')
        return redirect(detail_url(template))
    return editor(request, form, f'{"Edit" if option_pk else "Add"} choice — {row.title}', detail_url(template),
                  'Photos and files are stored privately. Client prices can be reused; internal costs are entered separately on each project.')


@login_required
@require_http_methods(['GET', 'POST'])
def option_delete(request, slug, template_pk, row_pk, option_pk):
    template = template_for(request.user, slug, template_pk)
    option = get_object_or_404(SelectionTemplateOption, row__template=template, row_id=row_pk, pk=option_pk)
    class ConfirmForm(forms.Form):
        confirm = forms.BooleanField(label='Remove this choice from this template')
    form = ConfirmForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            SelectionTemplate.objects.select_for_update().get(pk=template.pk, deleted_at__isnull=True)
            option.delete()
            template.save(update_fields=['updated_at'])
            audit(template, request.user, 'removed a choice from')
        return redirect(detail_url(template))
    return editor(request, form, 'Remove choice', detail_url(template), 'Project selections already loaded from this template stay unchanged.')


@login_required
@require_http_methods(['GET'])
def media(request, slug, template_pk, row_pk, option_pk, kind):
    template = template_for(request.user, slug, template_pk)
    option = get_object_or_404(SelectionTemplateOption, row__template=template, row_id=row_pk, pk=option_pk)
    if kind not in ('image', 'attachment'):
        raise Http404
    file = getattr(option, kind)
    if not file:
        raise Http404
    response = FileResponse(file.open('rb'), as_attachment=kind != 'image', filename=Path(file.name).name)
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required
@require_http_methods(['GET', 'POST'])
def save_project(request, pk):
    project = managed_project_or_404(request.user, pk)
    organization_for(request.user, project.organization.slug)
    form = SelectionSaveForm(request.POST or None, project=project,
                             initial={'selections': project.finish_selections.values_list('pk', flat=True)})
    if request.method == 'POST' and form.is_valid():
        template = save_project_template(project, request.user, form.cleaned_data['name'],
                                         form.cleaned_data['description'], form.cleaned_data['selections'])
        messages.success(request, 'Template saved. Approvals, client comments, dates, imported totals, and internal costs were not copied.')
        return redirect(detail_url(template))
    return editor(request, form, 'Save selections as a template', reverse('projects:selection_list', args=[pk]),
                  'Choose reusable rows. Categories, descriptions, allowances, choices, client prices, photos, and files are included. Client decisions, comments, internal costs, and old deadlines stay with the project.')


@login_required
@require_http_methods(['GET', 'POST'])
def load(request, pk, template_pk):
    project = managed_project_or_404(request.user, pk)
    template = template_for(request.user, project.organization.slug, template_pk)
    form = TemplateLoadForm(request.POST or None, template=template,
                            initial={'rows': template.rows.values_list('pk', flat=True)})
    if request.method == 'POST' and form.is_valid():
        added, skipped = load_template(template, project, request.user, form.cleaned_data['rows'], form.cleaned_data['due_date'])
        messages.success(request, f'{added} unreleased selections added; {skipped} existing rows skipped. Review prices and add internal costs before publishing.')
        return redirect('projects:selection_list', pk=pk)
    return editor(request, form, f'Load {template.name} into {project.name}', reverse('projects:selection_list', args=[pk]),
                  'Existing selections are kept. Rows already loaded, or with the same title, location, and category, are skipped. New rows start unreleased and need review before clients see them.')


@login_required
@require_http_methods(['GET', 'POST'])
def choose_project(request, slug, template_pk):
    template = template_for(request.user, slug, template_pk)
    available = projects_for_user(request.user).filter(organization=template.organization)
    allowed_ids = [project.pk for project in available if can_manage_project(request.user, project)]
    class ProjectForm(forms.Form):
        project = forms.ModelChoiceField(queryset=available.filter(pk__in=allowed_ids), label='Destination project')
    form = ProjectForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        return redirect('projects:selection_template_load', pk=form.cleaned_data['project'].pk, template_pk=template.pk)
    return editor(request, form, 'Choose a project', detail_url(template),
                  'Next, choose the rows and deadline to load. Nothing is added until you confirm that step.')
