from pathlib import Path

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.forms import inlineformset_factory
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .access import can_manage_project, projects_for_user
from .choice_images import saved_choice_files
from .forms import SelectionOptionForm
from .models import SelectionTemplate, SelectionTemplateOption, SelectionTemplateRow
from .selection_template_updates import (
    apply_template,
    project_revision,
    undo_replacement,
)
from .selection_templates import (
    audit,
    copy_template,
    save_project_template,
    template_organizations,
)
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
    mode = forms.ChoiceField(required=False, label='How should this project be updated?', widget=forms.RadioSelect,
        choices=(('amend', 'Amend — add new rows and missing choices; keep existing details and choices'),
                 ('replace', 'Replace — overwrite matching unreleased rows and their choices; add new rows'),
                 ('add', 'Add only — add new rows; skip existing rows entirely')))
    rows = forms.ModelMultipleChoiceField(queryset=None, widget=forms.CheckboxSelectMultiple)
    due_date = forms.DateField(required=False, label='New deadline (optional)', widget=forms.DateInput(attrs={'type': 'date'}))
    confirm_replace = forms.BooleanField(required=False,
        label='For Replace: I understand matching unreleased rows and choices will be overwritten, including prices and photos. Their internal costs reset to zero.',
        help_text='An undo record preserves the replaced data and files. Project-only rows, existing deadlines, and released or selected rows stay unchanged.')
    project_version = forms.CharField(required=False, widget=forms.HiddenInput)
    template_version = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, template, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['rows'].queryset = template.rows.annotate(choice_count=Count('options'))
        self.fields['rows'].label_from_instance = lambda row: f'{row.category + " / " if row.category else ""}{row.title} — {row.location or "No location"} ({row.choice_count} choices)'
        self.fields['due_date'].help_text = 'Applies to newly added rows only.'

    def clean(self):
        data = super().clean()
        if data.get('mode') == 'replace':
            if not data.get('confirm_replace'):
                self.add_error('confirm_replace', 'Confirm replacement, or choose Amend to preserve existing choices.')
            if not data.get('project_version') or not data.get('template_version'):
                raise forms.ValidationError('Reload the page to review the current selections before replacing them.')
        return data


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
    templates = organization.selection_templates.filter(deleted_at__isnull=request.GET.get('deleted') != '1').annotate(row_count=Count('rows', distinct=True), choice_count=Count('rows__options', distinct=True))
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
                            initial={'rows': template.rows.values_list('pk', flat=True), 'mode': 'amend',
                                     'template_version': template.updated_at.isoformat(),
                                     'project_version': project_revision(project)})
    if request.method == 'POST' and form.is_valid():
        try:
            result = apply_template(template, project, request.user, form.cleaned_data['rows'], form.cleaned_data['due_date'],
                mode=form.cleaned_data['mode'] or 'add', expected_revision=form.cleaned_data['project_version'],
                expected_template_version=form.cleaned_data['template_version'])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, f'{result.added} new rows added; {result.updated} existing rows updated; {result.choices_added} choices copied; {result.skipped} rows unchanged. Review new prices and internal costs before publishing.')
            if result.protected:
                messages.info(request, f'{result.protected} released or selected rows were protected.')
            if result.ambiguous:
                messages.warning(request, f'{result.ambiguous} rows had ambiguous matches and were left unchanged. Check duplicate titles, locations, and categories.')
            if result.backup_id:
                messages.info(request, 'An undo record was saved for replaced rows or historical-note cleanup. Open Template replacement history to undo before making further edits.')
            return redirect('projects:selection_list', pk=pk)
    return editor(request, form, f'Load {template.name} into {project.name}', reverse('projects:selection_list', args=[pk]),
                  f'This saved template contains {template.rows.count()} rows and {sum(r.choice_count for r in form.fields["rows"].queryset)} choices. Choose how to update this project, then select rows below. Choices include cover photos, galleries, captions, and files. Only unreleased rows can be amended or replaced; project-only rows stay unchanged. New rows start unreleased with no previous approvals. Amend also replaces recognized old export approval notes with the saved template description.',
                  project=project)


@login_required
@require_http_methods(['GET', 'POST'])
def replacement_history(request, pk):
    project = managed_project_or_404(request.user, pk)
    organization_for(request.user, project.organization.slug)
    if request.method == 'POST':
        backup = get_object_or_404(project.template_replacements, pk=request.POST.get('replacement'))
        if request.POST.get('confirm') != 'on':
            messages.error(request, 'Confirm undo before restoring replaced rows.')
        else:
            try:
                count = undo_replacement(backup, request.user)
            except ValidationError as error:
                messages.error(request, ' '.join(error.messages))
            else:
                messages.success(request, f'{count} replaced rows restored, including original choices, photos, and internal costs. Newly added rows were kept.')
        return redirect('projects:selection_template_replacements', pk=pk)
    page = Paginator(project.template_replacements.all(), 25).get_page(request.GET.get('page'))
    return render(request, 'projects/selection_template_replacements.html', {'project': project, 'page_obj': page})


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
