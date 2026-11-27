from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from .access import can_manage_project, projects_for_user
from .models import ActivityEvent, FinishSelection
from .services import record_activity


class DeadlineForm(forms.ModelForm):
    class Meta:
        model = FinishSelection
        fields = ('due_date', 'deadline_reminders_enabled')
        widgets = {'due_date': forms.DateInput(attrs={'type': 'date'})}


@login_required
@require_POST
@transaction.atomic
def update(request, pk, selection_pk):
    project = get_object_or_404(projects_for_user(request.user), pk=pk)
    if not can_manage_project(request.user, project):
        raise PermissionDenied
    selection = get_object_or_404(FinishSelection.objects.select_for_update(), project=project, pk=selection_pk)
    if selection.status not in ('draft', 'open'):
        raise PermissionDenied('Only pending selections can have a deadline changed.')
    form = DeadlineForm(request.POST, instance=selection)
    if form.is_valid():
        form.save()
        record_activity(organization=project.organization, project=project, actor=request.user,
            event_type=ActivityEvent.Type.SELECTION_DEADLINE_UPDATED,
            summary=f'Deadline settings updated for {selection.display_number}.',
            metadata={'selection_id': selection.pk})
        messages.success(request, 'Selection deadline settings saved.')
    else:
        messages.error(request, 'Enter a valid deadline date, or leave it blank to remove the deadline.')
    return redirect('projects:selection_detail', pk=pk, selection_pk=selection_pk)
