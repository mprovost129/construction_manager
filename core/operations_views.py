from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.template.loader import get_template
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from .operations import can_view_operations, database_probe, operations_snapshot
from .security import form_security


@method_decorator(never_cache, name='dispatch')
class OperationsView(LoginRequiredMixin, View):
    def get(self, request):
        if not can_view_operations(request.user):
            return HttpResponseForbidden('Developer access is required.')
        context = operations_snapshot()
        context.update(app_name=settings.APP_NAME, user=request.user)
        context.update(form_security(request))
        # No workspace processors: a broken project query must not take down this page.
        html = get_template('core/operations.html').render(context)
        return HttpResponse(html)


@method_decorator(never_cache, name='dispatch')
class HealthView(View):
    """Minimal external readiness probe. No accounts, settings or diagnostics exposed."""

    def get(self, request):
        healthy, _ = database_probe()
        return JsonResponse({'status': 'ok' if healthy else 'unavailable'},
                            status=200 if healthy else 503)
