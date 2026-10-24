#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from pathlib import Path

import sentry_sdk
from dotenv import load_dotenv
from sentry_sdk.integrations.excepthook import ExcepthookIntegration

from config.monitoring import configure_sentry, get_traces_sample_rate

BASE_DIR = Path(__file__).resolve().parent


def default_settings_module():
    load_dotenv(BASE_DIR / '.env')
    if os.environ.get('APP_ENVIRONMENT', '').strip().lower() == 'production':
        return 'config.settings.prod'
    return 'config.settings.dev'


def execute_command(execute_from_command_line, argv):
    try:
        execute_from_command_line(argv)
    except SystemExit as exc:
        if exc.code not in (None, 0):
            sentry_sdk.capture_message(
                'Django management command failed with a non-zero exit status.',
                level='error',
            )
            sentry_sdk.flush(timeout=2)
        raise
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        sentry_sdk.flush(timeout=2)
        raise


def configure_management_sentry(settings_module):
    if settings_module != 'config.settings.prod':
        return False

    os.environ['SENTRY_MANAGEMENT_COMMAND'] = '1'
    dsn = os.environ.get('SENTRY_DSN', '').strip()
    return configure_sentry(
        dsn=dsn,
        environment='production',
        release=os.environ.get('RENDER_GIT_COMMIT', ''),
        traces_sample_rate=get_traces_sample_rate(
            dsn,
            os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0.05'),
        ),
        disabled_integrations=(ExcepthookIntegration,),
    )


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', default_settings_module())
    configure_management_sentry(os.environ['DJANGO_SETTINGS_MODULE'])
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_command(execute_from_command_line, sys.argv)


if __name__ == '__main__':
    main()
