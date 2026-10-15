"""Isolated local PostgreSQL tests; never use the application's database as a test DB."""

import os
from copy import deepcopy

from django.core.exceptions import ImproperlyConfigured

from .base import DATABASES as configured_databases
from .test import *  # noqa: F403

DATABASES = deepcopy(configured_databases)
if DATABASES["default"]["HOST"] not in ("localhost", "127.0.0.1", "::1"):
    raise ImproperlyConfigured(
        "PostgreSQL acceptance tests require a local database server."
    )
DATABASES["default"]["NAME"] = "postgres"
DATABASES["default"]["TEST"] = {"NAME": f"test_cm_sync_{os.getpid()}"}
DATABASES["default"]["CONN_MAX_AGE"] = 0
