from django.views.generic import TemplateView

from projects.access import projects_for_user


class HomeView(TemplateView):
    template_name = 'core/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            context['projects'] = projects_for_user(self.request.user).select_related(
                'organization'
            )
        return context
