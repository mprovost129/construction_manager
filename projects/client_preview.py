"""Read-only, project-scoped client rendering; never changes the authenticated login."""
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from .access import internal_organizations_for_user, projects_for_user
from .models import ActivityEvent, OrganizationMembership, ProjectMembership
from .services import record_activity

SESSION_KEY = 'client_preview_project'

# Only reviewed, read-only client pages are available. New routes fail closed.
PROJECT_PAGES = frozenset('''detail action_center message_list message_thread message_create
document_list document_detail document_download client_upload document_signature_review
document_signature_certificate change_order_list change_order_detail estimate_list estimate_detail
selection_list selection_detail selection_package_detail selection_option_image selection_option_attachment
selection_choice_image selection_reference_image selection_custom_request_create
daily_log_list daily_log_detail daily_log_photo notification_settings punch_list punch_detail punch_photo'''.split())
BILLING_PAGES = frozenset(('invoice_list', 'invoice_detail', 'invoice_pdf', 'credit_memo_detail'))
CONTROL_PAGES = frozenset(('projects:client_preview_start', 'projects:client_preview_stop', 'logout'))


def preview_projects(user):
    return projects_for_user(user).filter(
        organization__in=internal_organizations_for_user(user)
    ).select_related('organization')


@login_required
@require_http_methods(['GET', 'POST'])
def start(request):
    if not internal_organizations_for_user(request.user).exists():
        raise PermissionDenied

    class PreviewForm(forms.Form):
        project = forms.ModelChoiceField(queryset=preview_projects(request.user), label='Project')

    form = PreviewForm(request.POST if request.method == 'POST' else None,
                       initial={'project': request.GET.get('project')})
    if request.method == 'POST' and form.is_valid():
        project = form.cleaned_data['project']
        record_activity(organization=project.organization, project=project, actor=request.user,
                        event_type=ActivityEvent.Type.CLIENT_PREVIEW_STARTED,
                        summary=f'{request.user.email} opened read-only client preview.')
        request.session[SESSION_KEY] = project.pk
        return redirect('projects:detail', pk=project.pk)
    return render(request, 'projects/client_preview_start.html', {'form': form})


@login_required
@require_POST
def stop(request):
    project_id = request.session.pop(SESSION_KEY, None)
    project = preview_projects(request.user).filter(pk=project_id).first() if project_id else None
    if project:
        record_activity(organization=project.organization, project=project, actor=request.user,
                        event_type=ActivityEvent.Type.CLIENT_PREVIEW_STOPPED,
                        summary=f'{request.user.email} returned to staff view.')
        return redirect('projects:detail', pk=project_id)
    return redirect('core:home')


class ClientPreviewMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        project_id = request.session.get(SESSION_KEY)
        if not project_id or not request.user.is_authenticated:
            return None
        name = request.resolver_match.view_name
        # These controls always act as the staff account, never the preview client.
        if name in CONTROL_PAGES or name.startswith('users:mfa_'):
            return None
        request.client_preview_actor = request.user
        request.client_preview_active = True
        project = preview_projects(request.user).filter(pk=project_id).first()
        request.client_preview_project = project
        if project is None:
            return render(request, 'projects/client_preview_blocked.html', status=403)
        # Only use a primary client's identity for per-client decisions/signature links.
        # Never render as a privileged account, even if it also has a client assignment.
        membership = project.project_memberships.filter(
            is_active=True, role=OrganizationMembership.Role.CLIENT,
            client_access_type=ProjectMembership.ClientAccessType.PRIMARY,
            user__is_active=True, user__is_staff=False, user__is_superuser=False,
        ).exclude(user__organization_memberships__role__in=OrganizationMembership.INTERNAL_ROLES,
                  user__organization_memberships__is_active=True).select_related('user').first()
        if membership:
            client = membership.user
        else:
            # Unsaved synthetic identity: no account, invitation or membership is created.
            synthetic_id = -request.client_preview_actor.pk
            if get_user_model().objects.filter(pk=synthetic_id).exists():
                return render(request, 'projects/client_preview_blocked.html', status=403)
            client = get_user_model()(pk=synthetic_id, email='Client preview')
        client._client_preview_project_id = project.pk
        request.user = client
        request.client_preview_membership = membership
        if request.method not in ('GET', 'HEAD'):
            return render(request, 'projects/client_preview_blocked.html', status=403)
        if name == 'core:home':
            return redirect('projects:detail', pk=project.pk)
        if name in ('core:help', 'core:eula', 'core:privacy', 'core:service_worker'):
            return None
        allowed = (request.resolver_match.namespace == 'projects' and request.resolver_match.url_name in PROJECT_PAGES
                   or request.resolver_match.namespace == 'billing' and request.resolver_match.url_name in BILLING_PAGES)
        route_project = view_kwargs.get('pk', view_kwargs.get('project_id'))
        if not allowed or route_project != project.pk:
            return render(request, 'projects/client_preview_blocked.html', status=403)
        return None


def preview_navigation(request):
    active = getattr(request, 'client_preview_active', False)
    actor = getattr(request, 'client_preview_actor', request.user)
    return {
        'client_preview_active': active,
        'client_preview_project': getattr(request, 'client_preview_project', None),
        'client_preview_actor': actor,
        'can_preview_client': not active and internal_organizations_for_user(actor).exists(),
    }
