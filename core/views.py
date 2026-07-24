from django.views.generic import TemplateView

from projects.access import projects_for_user
from projects.action_center import build_portfolio_action_center


class HomeView(TemplateView):
    template_name = 'core/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            projects = list(
                projects_for_user(self.request.user).select_related('organization')
            )
            context['projects'] = projects
            portfolio = build_portfolio_action_center(
                self.request.user,
                projects,
            )
            if portfolio['project_summaries']:
                context['portfolio_action_center'] = portfolio
                for summary in portfolio['project_summaries']:
                    summary['project'].home_action_summary = summary
        return context
