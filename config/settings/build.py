"""Settings used only while building deployable static assets."""

import os

# The production settings module validates these runtime values at import time.
# Build-only placeholders keep collectstatic deterministic without embedding secrets.
os.environ.setdefault("SECRET_KEY", "build-only-placeholder-not-used-at-runtime")
os.environ.setdefault("ALLOWED_HOSTS", "localhost")
os.environ.setdefault("DB_NAME", "build_only")
os.environ.setdefault("DB_USER", "build_only")
os.environ.setdefault("DB_PASSWORD", "build_only")
os.environ.setdefault("DB_HOST", "localhost")

from .prod import *  # noqa: E402,F403
