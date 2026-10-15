"""Public health probes return status only, without client data or exception details."""

import logging
from uuid import uuid4

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

logger = logging.getLogger('django')


@never_cache
@require_safe
def live(request):
    # No session, database, cache, or external service is needed for liveness.
    return JsonResponse({'status': 'ok'})


def _database_ready():
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            return cursor.fetchone() == (1,)
    except Exception:
        # Credentials, hostnames and driver errors must not appear in this probe.
        logger.warning('Readiness check failed: database unavailable.')
        return False


def _cache_ready():
    key = f'health:ready:{uuid4().hex}'
    marker = uuid4().hex
    try:
        try:
            cache.set(key, marker, timeout=15)
            return cache.get(key) == marker
        finally:
            # Isolated short-lived keys never replace or clear application data.
            cache.delete(key)
    except Exception:
        logger.warning('Readiness check failed: cache unavailable.')
        return False


@never_cache
@require_safe
def ready(request):
    # Fail fast when the database is unavailable. Never call third-party APIs.
    healthy = _database_ready() and _cache_ready()
    return JsonResponse(
        {'status': 'ok' if healthy else 'unavailable'},
        status=200 if healthy else 503,
    )
