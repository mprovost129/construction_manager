#!/bin/sh
set -eu

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.prod}"

if [ "${RUN_STARTUP_TASKS:-true}" = "true" ]; then
    python manage.py migrate --noinput
    python manage.py bootstrap_superuser
else
    # Workers share the web database; never race its migration/bootstrap phase.
    python manage.py migrate --check
fi

exec "$@"
