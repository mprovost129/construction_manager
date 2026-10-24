import os

from config.monitoring import configure_sentry, get_traces_sample_rate

os.environ['APP_ENVIRONMENT'] = 'production'

from .base import *  # noqa: E402,F403

DEBUG = False

ALLOWED_HOSTS = [host.strip() for host in os.environ['ALLOWED_HOSTS'].split(',') if host.strip()]

# Whitenoise — insert after SecurityMiddleware
MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')  # noqa: F405

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

# Persistent DB connections
CONN_MAX_AGE = 60

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',')
    if origin.strip()
]

CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379/1'),
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        },
    }
}

# HTTPS / security
SECURE_SSL_REDIRECT = env_bool('SECURE_SSL_REDIRECT', True)  # noqa: F405
SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '31536000'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool(  # noqa: F405
    'SECURE_HSTS_INCLUDE_SUBDOMAINS', True
)
SECURE_HSTS_PRELOAD = env_bool('SECURE_HSTS_PRELOAD', True)  # noqa: F405
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_AGE = int(os.environ.get('SESSION_COOKIE_AGE', '43200'))

if env_bool('TRUST_PROXY_SSL_HEADER', False):  # noqa: F405
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

if env_bool('DB_SSL_REQUIRE', True):  # noqa: F405
    DATABASES['default']['OPTIONS'] = {'sslmode': 'require'}  # noqa: F405

# Error monitoring is enabled only when a production DSN is supplied. Render
# exposes its source commit automatically, giving each event an exact release.
SENTRY_DSN = os.environ.get('SENTRY_DSN', '').strip()
SENTRY_TRACES_SAMPLE_RATE = get_traces_sample_rate(
    SENTRY_DSN,
    os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0.05'),
)
SENTRY_ENABLED = configure_sentry(
    dsn=SENTRY_DSN,
    environment=APP_ENVIRONMENT,  # noqa: F405
    release=os.environ.get('RENDER_GIT_COMMIT', ''),
    traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
)
