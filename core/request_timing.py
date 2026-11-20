"""Privacy-safe, database-independent diagnostics for slow WSGI requests."""

import logging
from time import perf_counter

logger = logging.getLogger('heritage.request_timing')
STANDARD_METHODS = frozenset({'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'})


class RequestTimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = perf_counter()

        def report(status, phase):
            # Never access URL/path, parameters, headers, bodies, user or session.
            # Named routes come from application code, not captured URL arguments.
            try:
                elapsed = perf_counter() - started
                match = getattr(request, 'resolver_match', None)
                route = match.view_name if match and match.url_name else 'unmatched'
                threshold = 1 if route == 'core:health' else 2
                if elapsed < threshold and status < 500:
                    return
                method = request.method if request.method in STANDARD_METHODS else 'OTHER'
                logger.warning(
                    'request_timing route=%s method=%s status=%d duration_ms=%d phase=%s',
                    route, method, status, round(elapsed * 1000), phase,
                )
            except Exception:
                # Observability failures must never break the original request.
                pass

        try:
            response = self.get_response(request)
        except Exception:
            report(500, 'exception')
            raise
        report(response.status_code, 'response')
        if response.streaming:
            # Retain FileResponse's native file wrapper and all resource closers.
            # A stream-close timing includes transfer/cleanup time; it does NOT
            # claim the entire download reached the client successfully.
            close_response = response.close
            reported = False

            def close():
                nonlocal reported
                try:
                    close_response()
                finally:
                    if not reported:
                        reported = True
                        report(response.status_code, 'stream_close')

            response.close = close
        return response
