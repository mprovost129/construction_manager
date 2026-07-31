#!/bin/sh
set -eu

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.Settings.prod}"

python manage.py migrate --noinput
python manage.py bootstrap_superuser

exec "$@"
