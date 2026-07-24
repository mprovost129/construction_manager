from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.views.generic import ListView, TemplateView

from projects.access import internal_organizations_for_user, projects_for_user
from projects.action_center import build_portfolio_action_center
from projects.models import ActivityEvent, Project

PROJECT_ACTIVITY_TYPE_CHOICES = tuple(
    (value, label)
    for value, label in ActivityEvent.Type.choices
    if not value.startswith('team_')
)
PROJECT_ACTIVITY_TYPE_VALUES = {
    value for value, _label in PROJECT_ACTIVITY_TYPE_CHOICES
}


class HomeView(TemplateView):
    template_name = 'core/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            all_projects = list(
                projects_for_user(self.request.user).select_related('organization')
            )
            project_search = self.request.GET.get('q', '').strip()[:100]
            requested_status = self.request.GET.get('status', '').strip()
            project_status = (
                requested_status
                if requested_status in Project.Status.values
                else ''
            )

            projects = all_projects
            if project_search:
                normalized_search = project_search.casefold()
                projects = [
                    project
                    for project in projects
                    if normalized_search in project.name.casefold()
                    or normalized_search in project.code.casefold()
                    or normalized_search in project.organization.name.casefold()
                ]
            if project_status:
                projects = [
                    project
                    for project in projects
                    if project.status == project_status
                ]

            context.update(
                {
                    'projects': projects,
                    'all_project_count': len(all_projects),
                    'project_result_count': len(projects),
                    'project_search': project_search,
                    'project_status': project_status,
                    'project_status_choices': Project.Status.choices,
                    'has_project_filters': bool(
                        project_search or project_status
                    ),
                }
            )
            portfolio = build_portfolio_action_center(
                self.request.user,
                all_projects,
            )
            if portfolio['project_summaries']:
                context['portfolio_action_center'] = portfolio
                for summary in portfolio['project_summaries']:
                    summary['project'].home_action_summary = summary
                internal_project_ids = [
                    summary['project'].pk
                    for summary in portfolio['project_summaries']
                    if not summary['viewer_is_client']
                ]
                if internal_project_ids:
                    context['show_portfolio_activity'] = True
                    context['recent_activity_events'] = list(
                        ActivityEvent.objects.filter(
                            project_id__in=internal_project_ids
                        ).select_related(
                            'actor',
                            'project__organization',
                        )[:12]
                    )
        return context


class ProjectActivityListView(LoginRequiredMixin, ListView):
    template_name = 'core/activity_list.html'
    context_object_name = 'activity_events'
    paginate_by = 25

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        organizations = internal_organizations_for_user(
            request.user,
            management_only=True,
        )
        if not request.user.is_superuser and not organizations.exists():
            raise PermissionDenied(
                'Only company administrators and staff can view project activity.'
            )
        self.projects = list(
            Project.objects.filter(organization__in=organizations).select_related(
                'organization'
            )
        )
        self.project_ids = {project.pk for project in self.projects}
        self.activity_search = request.GET.get('q', '').strip()[:100]
        requested_project = request.GET.get('project', '').strip()
        self.activity_project = (
            int(requested_project)
            if requested_project.isdigit()
            and int(requested_project) in self.project_ids
            else None
        )
        requested_type = request.GET.get('type', '').strip()
        self.activity_type = (
            requested_type
            if requested_type in PROJECT_ACTIVITY_TYPE_VALUES
            else ''
        )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = ActivityEvent.objects.filter(
            project_id__in=self.project_ids,
            event_type__in=PROJECT_ACTIVITY_TYPE_VALUES,
        ).select_related(
            'actor',
            'project__organization',
        )
        if self.activity_search:
            queryset = queryset.filter(
                Q(summary__icontains=self.activity_search)
                | Q(actor__email__icontains=self.activity_search)
                | Q(project__name__icontains=self.activity_search)
                | Q(project__code__icontains=self.activity_search)
            )
        if self.activity_project:
            queryset = queryset.filter(project_id=self.activity_project)
        if self.activity_type:
            queryset = queryset.filter(event_type=self.activity_type)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query_parameters = self.request.GET.copy()
        query_parameters.pop('page', None)
        context.update(
            {
                'projects': self.projects,
                'activity_search': self.activity_search,
                'activity_project': self.activity_project,
                'activity_type': self.activity_type,
                'activity_type_choices': PROJECT_ACTIVITY_TYPE_CHOICES,
                'has_activity_filters': bool(
                    self.activity_search
                    or self.activity_project
                    or self.activity_type
                ),
                'activity_querystring': query_parameters.urlencode(),
            }
        )
        return context
