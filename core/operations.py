from datetime import timedelta
from time import perf_counter
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Count
from django.utils import timezone

from config.monitoring import sentry_configuration_is_active
from projects.models import ActivityEvent, Project, ProjectDocumentVersion

from .models import OperationalEvent


def can_view_operations(user):
    return bool(
        user.is_authenticated and user.is_active
        and (user.is_superuser or user.has_perm('core.view_operations'))
    )


def record_operational_event(kind):
    """Telemetry must not break the original request or email workflow."""
    try:
        with transaction.atomic():
            OperationalEvent.objects.create(kind=kind)
    except Exception:
        # A fixed stderr warning avoids passing exception/credential data to Sentry.
        import sys
        try:
            print('Warning: operational telemetry could not be recorded.', file=sys.stderr)
        except Exception:
            pass


def database_probe():
    started = perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            if cursor.fetchone() != (1,):
                return False, None
    except Exception:
        return False, None
    return True, round((perf_counter() - started) * 1000, 1)


def provider_links():
    """Allow only ordinary HTTPS links, never embedded credentials or tokens."""
    providers = (
        ('Render', 'https://dashboard.render.com/'),
        ('Cloudflare', 'https://dash.cloudflare.com/'),
        ('Resend', 'https://resend.com/emails'),
        ('Sentry', 'https://sentry.io/'),
        ('Better Stack', 'https://uptime.betterstack.com/'),
        ('GitHub', 'https://github.com/'),
    )
    links = []
    for name, fallback in providers:
        candidate = settings.OPERATIONS_DASHBOARD_URLS.get(name, '') or fallback
        try:
            parsed = urlsplit(candidate)
            valid = (
                parsed.scheme == 'https' and parsed.hostname
                and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment
            )
        except ValueError:
            valid = False
        links.append({'name': name, 'url': candidate if valid else fallback})
    return links


def operations_snapshot():
    checked_at = timezone.now()
    database_ok, latency = database_probe()
    result = {
        'checked_at': checked_at,
        'database_ok': database_ok,
        'database_latency': latency,
        'environment': settings.APP_ENVIRONMENT,
        'release': settings.APP_RELEASE[:40] or 'Not supplied',
        'providers': provider_links(),
        'metrics': None,
        'events': [],
        'activity': [],
        'sentry_active': False,
        'r2_selected': settings.PRIVATE_STORAGE_BACKEND == 'r2',
        'email_external': settings.EMAIL_BACKEND not in {
            'django.core.mail.backends.console.EmailBackend',
            'django.core.mail.backends.locmem.EmailBackend',
            'django.core.mail.backends.filebased.EmailBackend',
            'django.core.mail.backends.dummy.EmailBackend',
        },
        'turnstile_configured': bool(
            settings.TURNSTILE_SITE_KEY and settings.TURNSTILE_SECRET_KEY
        ),
    }
    try:
        result['sentry_active'] = sentry_configuration_is_active()
    except Exception:
        pass
    if database_ok:
        try:
            with transaction.atomic():
                counts = dict(
                    OperationalEvent.objects.filter(
                        created_at__gte=checked_at - timedelta(hours=24),
                    ).values('kind').annotate(total=Count('pk')).values_list('kind', 'total')
                )
                metrics = {
                    'projects': Project.objects.count(),
                    'active_users': get_user_model().objects.filter(is_active=True).count(),
                    'document_versions': ProjectDocumentVersion.objects.count(),
                    'request_failures': counts.get(OperationalEvent.Kind.REQUEST_FAILED, 0),
                    'email_failures': counts.get(OperationalEvent.Kind.EMAIL_FAILED, 0),
                    'email_accepted': counts.get(OperationalEvent.Kind.EMAIL_ACCEPTED, 0),
                    'email_test': counts.get(OperationalEvent.Kind.EMAIL_TEST, 0),
                }
                events = list(OperationalEvent.objects.all()[:15])
                # Fetch only event types and timestamps, never audit payloads or client text.
                activity = list(ActivityEvent.objects.only('event_type', 'created_at')
                                .order_by('-created_at', '-pk')[:10])
            result.update(metrics=metrics, events=events, activity=activity)
        except Exception:
            # Includes missing migrations; connectivity and schema health are distinct.
            pass
    return result


class OperationalFailureMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code >= 500 and request.path != '/health/':
            record_operational_event(OperationalEvent.Kind.REQUEST_FAILED)
        return response
