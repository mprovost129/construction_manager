import csv
from datetime import date, datetime, time
from urllib.parse import urlencode

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import StreamingHttpResponse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, TemplateView

from projects.access import projects_for_user
from projects.action_center import build_portfolio_action_center
from projects.models import ActivityEvent, OrganizationMembership, Project

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


class ProjectActivityAccessMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        can_view_activity = request.user.is_superuser or request.user.organization_memberships.filter(
            is_active=True,
            role__in=tuple(
                role
                for role in OrganizationMembership.INTERNAL_ROLES
                if role != OrganizationMembership.Role.ACCOUNTANT
            ),
        ).exists()
        if not can_view_activity:
            raise PermissionDenied(
                'Only assigned internal project users can view project activity.'
            )
        self.projects = list(
            projects_for_user(request.user).select_related('organization')
        )
        self.project_ids = {project.pk for project in self.projects}
        self.activity_search = request.GET.get('q', '').strip()[:100]
        requested_project = request.GET.get('project', '').strip()
        try:
            requested_project_id = int(requested_project)
        except ValueError:
            requested_project_id = None
        self.activity_project = (
            requested_project_id
            if requested_project_id in self.project_ids
            else None
        )
        requested_type = request.GET.get('type', '').strip()
        self.activity_type = (
            requested_type
            if requested_type in PROJECT_ACTIVITY_TYPE_VALUES
            else ''
        )
        self.activity_from_date = self.parse_date(
            request.GET.get('from', '').strip()
        )
        self.activity_to_date = self.parse_date(
            request.GET.get('to', '').strip()
        )
        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def parse_date(value):
        try:
            return date.fromisoformat(value) if value else None
        except ValueError:
            return None

    @staticmethod
    def date_boundary(value, boundary_time):
        return timezone.make_aware(
            datetime.combine(value, boundary_time),
            timezone.get_current_timezone(),
        )

    def get_activity_queryset(self):
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
        if self.activity_from_date:
            queryset = queryset.filter(
                created_at__gte=self.date_boundary(
                    self.activity_from_date,
                    time.min,
                )
            )
        if self.activity_to_date:
            queryset = queryset.filter(
                created_at__lte=self.date_boundary(
                    self.activity_to_date,
                    time.max,
                )
            )
        return queryset

    @property
    def has_activity_filters(self):
        return bool(
            self.activity_search
            or self.activity_project
            or self.activity_type
            or self.activity_from_date
            or self.activity_to_date
        )

    @property
    def activity_querystring(self):
        parameters = []
        if self.activity_search:
            parameters.append(('q', self.activity_search))
        if self.activity_project:
            parameters.append(('project', self.activity_project))
        if self.activity_type:
            parameters.append(('type', self.activity_type))
        if self.activity_from_date:
            parameters.append(('from', self.activity_from_date.isoformat()))
        if self.activity_to_date:
            parameters.append(('to', self.activity_to_date.isoformat()))
        return urlencode(parameters)

    def activity_context(self):
        return {
            'projects': self.projects,
            'activity_search': self.activity_search,
            'activity_project': self.activity_project,
            'activity_type': self.activity_type,
            'activity_from_date': self.activity_from_date,
            'activity_to_date': self.activity_to_date,
            'activity_type_choices': PROJECT_ACTIVITY_TYPE_CHOICES,
            'has_activity_filters': self.has_activity_filters,
            'activity_querystring': self.activity_querystring,
        }


class ProjectActivityListView(ProjectActivityAccessMixin, ListView):
    template_name = 'core/activity_list.html'
    context_object_name = 'activity_events'
    paginate_by = 25

    def get_queryset(self):
        return self.get_activity_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.activity_context())
        return context


class CSVBuffer:
    def write(self, value):
        return value


def spreadsheet_safe(value):
    text = '' if value is None else str(value)
    stripped = text.lstrip()
    if stripped.startswith(('=', '+', '-', '@')) or text.startswith(
        ('\t', '\r', '\n')
    ):
        return f"'{text}"
    return text


class ProjectActivityExportView(ProjectActivityAccessMixin, View):
    def get(self, request, *args, **kwargs):
        writer = csv.writer(CSVBuffer(), lineterminator='\r\n')

        def rows():
            yield writer.writerow(
                (
                    'Timestamp',
                    'Company',
                    'Project',
                    'Project code',
                    'Event type',
                    'Event label',
                    'Summary',
                    'Actor email',
                )
            )
            events = self.get_activity_queryset().iterator(chunk_size=1000)
            for event in events:
                yield writer.writerow(
                    tuple(
                        spreadsheet_safe(value)
                        for value in (
                            timezone.localtime(event.created_at).isoformat(),
                            event.project.organization.name,
                            event.project.name,
                            event.project.code,
                            event.event_type,
                            event.get_event_type_display(),
                            event.summary,
                            event.actor.email if event.actor else '',
                        )
                    )
                )

        response = StreamingHttpResponse(
            rows(),
            content_type='text/csv; charset=utf-8',
        )
        filename = f'project-activity-{timezone.localdate().isoformat()}.csv'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
