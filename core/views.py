from django.views.generic import TemplateView

from projects.access import projects_for_user
from projects.action_center import build_portfolio_action_center
from projects.models import Project


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
        return context
