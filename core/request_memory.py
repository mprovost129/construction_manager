"""Resource diagnostics without client identities, file names, or request data."""

import logging
import os
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger('heritage.request_memory')


def memory_snapshot():
    rss_kb = container_kb = -1
    try:
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                rss_kb = int(line.split()[1])
                break
    except (OSError, ValueError, IndexError):
        pass
    try:
        container_kb = int(Path('/sys/fs/cgroup/memory.current').read_text()) // 1024
    except (OSError, ValueError):
        pass
    return rss_kb, container_kb


class RequestMemoryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def report(self, request, phase, status=0):
        try:
            trace = getattr(request, '_resource_trace', None)
            if trace is None:
                return
            rss, container = memory_snapshot()
            logger.info(
                'request_memory trace=%s route=%s pid=%d phase=%s status=%d rss_kb=%d container_kb=%d',
                trace, request.resolver_match.view_name, os.getpid(), phase,
                status, rss, container,
            )
        except Exception:
            pass  # Diagnostics cannot break the request they are observing.

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        if match and match.url_name and match.namespace == 'projects':
            request._resource_trace = uuid4().hex[:12]
            self.report(request, 'start')

    def __call__(self, request):
        response = self.get_response(request)
        self.report(request, 'response', response.status_code)
        if response.streaming and getattr(request, '_resource_trace', None):
            original_close = response.close
            reported = False

            def close():
                nonlocal reported
                try:
                    original_close()
                finally:
                    if not reported:
                        reported = True
                        self.report(request, 'stream_close', response.status_code)

            response.close = close
        return response
