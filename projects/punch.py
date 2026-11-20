"""Project-scoped punch lists. Sharing never grants write access."""
import uuid

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .access import (
    can_manage_project,
    can_use_project_messaging,
    is_project_client,
    projects_for_user,
)
from .models import ActivityEvent, OrganizationMembership, PunchItem, PunchPhoto
from .services import record_activity


def staff_for(project):
    memberships = OrganizationMembership.objects.filter(
        organization=project.organization, is_active=True,
        role__in=OrganizationMembership.INTERNAL_ROLES,
    ).exclude(role=OrganizationMembership.Role.ACCOUNTANT).filter(
        Q(role=OrganizationMembership.Role.ADMIN)
        | Q(project_access__project=project, project_access__is_active=True)
    )
    return get_user_model().objects.filter(is_active=True, pk__in=memberships.values('user_id'))


def scope(request, pk):
    project = get_object_or_404(projects_for_user(request.user), pk=pk)
    if not can_use_project_messaging(request.user, project):
        raise PermissionDenied
    internal = not is_project_client(request.user, project) and (
        request.user.is_superuser or staff_for(project).filter(pk=request.user.pk).exists()
    )
    items = project.punch_items.all()
    if not internal:
        items = items.filter(client_visible=True)
    return project, internal, items


class ItemForm(forms.ModelForm):
    class Meta:
        model = PunchItem
        fields = ('title', 'description', 'location', 'assigned_to', 'due_date', 'deadline_reminders_enabled', 'priority',
                  'client_visible', 'internal_notes')
        widgets = {'due_date': forms.DateInput(attrs={'type': 'date'}),
                   'description': forms.Textarea(attrs={'rows': 3}),
                   'internal_notes': forms.Textarea(attrs={'rows': 3})}
        labels = {'client_visible': 'Share this item and its photos with clients',
                  'description': 'Description (visible when shared)'}

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_to'].queryset = staff_for(project)


class PhotoForm(forms.Form):
    image = forms.ImageField(help_text='JPEG, PNG or WebP, up to 10 MB. Photos follow the item’s sharing setting.')
    caption = forms.CharField(max_length=200, required=False)

    def clean_image(self):
        image = self.cleaned_data['image']
        if image.size > 10 * 1024 * 1024 or image.image.format not in ('JPEG', 'PNG', 'WEBP'):
            raise forms.ValidationError('Use a JPEG, PNG or WebP image no larger than 10 MB.')
        # Never use a supplied filename or browser content type when serving the file.
        image.name = f'{uuid.uuid4().hex}.{image.image.format.lower()}'
        return image


def audit(request, item, action):
    record_activity(organization=item.project.organization, project=item.project, actor=request.user,
                    event_type=ActivityEvent.Type.PUNCH_UPDATED,
                    summary=f'Punch-list item #{item.pk}: {action}.',
                    metadata={'punch_item_id': item.pk, 'action': action})


@login_required
@require_http_methods(['GET', 'HEAD'])
def listing(request, pk):
    project, internal, items = scope(request, pk)
    status = request.GET.get('status', 'active')
    if status == 'active':
        items = items.exclude(status__in=('verified', 'cancelled'))
    elif status in PunchItem.Status.values:
        items = items.filter(status=status)
    if request.GET.get('mine') == '1' and internal:
        items = items.filter(assigned_to=request.user)
    return render(request, 'projects/punch_list.html', {
        'project': project, 'items': items.select_related('assigned_to'), 'internal': internal,
        'can_manage': internal and can_manage_project(request.user, project),
        'statuses': PunchItem.Status.choices, 'status_filter': status,
        'mine': request.GET.get('mine') == '1',
    })


@login_required
@require_http_methods(['GET', 'HEAD', 'POST'])
@transaction.atomic
def edit(request, pk, item_pk=None):
    project, internal, items = scope(request, pk)
    if not internal or not can_manage_project(request.user, project):
        raise PermissionDenied
    if request.method == 'POST':
        items = items.select_for_update()
    item = get_object_or_404(items, pk=item_pk) if item_pk else PunchItem(project=project, created_by=request.user)
    form = ItemForm(request.POST if request.method == 'POST' else None, instance=item, project=project)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            form.save()
            audit(request, item, 'details saved')
        messages.success(request, 'Punch-list item saved.')
        return redirect('projects:punch_detail', pk=pk, item_pk=item.pk)
    return render(request, 'projects/punch_form.html', {'project': project, 'form': form, 'item': item})


@login_required
@require_http_methods(['GET', 'HEAD', 'POST'])
def detail(request, pk, item_pk):
    project, internal, items = scope(request, pk)
    item = get_object_or_404(items.select_related('assigned_to', 'verified_by'), pk=item_pk)
    manager = internal and can_manage_project(request.user, project)
    can_work = internal and (manager or item.assigned_to_id == request.user.pk)
    photo_form = PhotoForm()
    if request.method == 'POST':
        if not can_work:
            raise PermissionDenied
        action = request.POST.get('action')
        with transaction.atomic():
            item = get_object_or_404(items.select_for_update(), pk=item_pk)
            # Recheck after locking in case someone reassigned the item.
            if not manager and item.assigned_to_id != request.user.pk:
                raise PermissionDenied
            if action == 'photo':
                if item.status in ('verified', 'cancelled'):
                    raise PermissionDenied('Reopen the item before adding photos.')
                photo_form = PhotoForm(request.POST, request.FILES)
                if photo_form.is_valid():
                    if item.photos.count() >= 20:
                        photo_form.add_error(None, 'Each item can have up to 20 photos.')
                    else:
                        PunchPhoto.objects.create(item=item, uploaded_by=request.user, **photo_form.cleaned_data)
                        audit(request, item, 'photo added')
                        return redirect('projects:punch_detail', pk=pk, item_pk=item.pk)
            elif action == 'remove_photo' and manager:
                photo = get_object_or_404(item.photos, pk=request.POST.get('photo_id'))
                photo.delete()  # Keep stored bytes for backup retention; access is immediately revoked.
                audit(request, item, 'photo removed')
                return redirect('projects:punch_detail', pk=pk, item_pk=item.pk)
            else:
                allowed = {'open': ('in_progress', 'ready'), 'in_progress': ('ready',), 'ready': ()}
                if manager:
                    allowed = {state: ('open', 'in_progress', 'ready', 'cancelled') for state in PunchItem.Status.values}
                    allowed['ready'] += ('verified',)
                if action not in allowed.get(item.status, ()):
                    raise PermissionDenied('That status change is not available.')
                item.status = action
                item.verified_by = request.user if action == 'verified' else None
                item.verified_at = timezone.now() if action == 'verified' else None
                item.save(update_fields=('status', 'verified_by', 'verified_at', 'updated_at'))
                audit(request, item, f'status changed to {item.get_status_display()}')
                messages.success(request, 'Punch-list status updated.')
                return redirect('projects:punch_detail', pk=pk, item_pk=item.pk)
    return render(request, 'projects/punch_detail.html', {
        'project': project, 'item': item, 'internal': internal, 'can_manage': manager,
        'can_work': can_work, 'photo_form': photo_form,
    })


@login_required
@require_http_methods(['GET', 'HEAD'])
def photo(request, pk, item_pk, photo_pk):
    _, _, items = scope(request, pk)
    item = get_object_or_404(items, pk=item_pk)
    photo = get_object_or_404(item.photos, pk=photo_pk)
    extension = photo.image.name.rsplit('.', 1)[-1]
    response = FileResponse(photo.image.open('rb'), content_type={
        'jpeg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp',
    }.get(extension, 'application/octet-stream'))
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
