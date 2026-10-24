#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from pathlib import Path

import sentry_sdk
from dotenv import load_dotenv
from sentry_sdk.integrations.excepthook import ExcepthookIntegration

from config.monitoring import (
    configure_sentry,
    get_traces_sample_rate,
    get_verified_sentry_client,
)

BASE_DIR = Path(__file__).resolve().parent


def default_settings_module():
    load_dotenv(BASE_DIR / '.env')
    if os.environ.get('APP_ENVIRONMENT', '').strip().lower() == 'production':
        return 'config.settings.prod'
    return 'config.settings.dev'


def load_django_command():
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    return execute_from_command_line


def execute_command(argv, command_loader=None):
    try:
        if command_loader is None:
            command_loader = load_django_command
        execute_from_command_line = command_loader()
        execute_from_command_line(argv)
    except SystemExit as exc:
        if exc.code not in (None, 0):
            report_management_failure(exit_failure=True)
        raise
    except Exception as exc:
        report_management_failure(exception=exc)
        raise


def report_management_failure(*, exception=None, exit_failure=False):
    """Report a command failure without ever replacing the original error."""
    try:
        client = ensure_management_sentry()
        if not client:
            return False
        scope = sentry_sdk.Scope()
        scope.set_client(client)
        if exit_failure:
            scope.capture_message(
                'Django management command failed with a non-zero exit status.',
                level='error',
            )
        else:
            scope.capture_exception(exception)
        pending_events = 0

        def record_pending_events(pending, timeout):
            nonlocal pending_events
            pending_events = pending

        client.flush(timeout=2, callback=record_pending_events)
        if pending_events:
            surface_monitoring_failure()
            return False
    except Exception:
        surface_monitoring_failure()
        return False
    return True


def surface_monitoring_failure():
    """Warn on stderr without exposing error details to logging integrations."""
    try:
        print(
            'Warning: Sentry monitoring could not report the management command failure.',
            file=sys.stderr,
        )
    except Exception:
        pass


def configure_management_sentry(settings_module):
    if settings_module != 'config.settings.prod':
        return False

    os.environ['SENTRY_MANAGEMENT_COMMAND'] = '1'
    return True


def ensure_management_sentry():
    if os.environ.get('SENTRY_MANAGEMENT_COMMAND') != '1':
        return False
    dsn = os.environ.get('SENTRY_DSN', '').strip()
    if not dsn:
        return False
    release = os.environ.get('RENDER_GIT_COMMIT', '')
    traces_sample_rate = get_traces_sample_rate(
        dsn,
        os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0.05'),
    )
    configuration = {
        'dsn': dsn,
        'environment': 'production',
        'release': release,
        'traces_sample_rate': traces_sample_rate,
        'disabled_integrations': (ExcepthookIntegration,),
    }
    try:
        client = get_verified_sentry_client(**configuration)
        if client is not None:
            return client
        if not configure_sentry(**configuration):
            surface_monitoring_failure()
            return None
        return get_verified_sentry_client(**configuration)
    except Exception:
        surface_monitoring_failure()
        return None


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', default_settings_module())
    configure_management_sentry(os.environ['DJANGO_SETTINGS_MODULE'])
    execute_command(sys.argv)


if __name__ == '__main__':
    main()
