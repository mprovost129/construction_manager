import os
from pathlib import Path

from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
# config/Settings/base.py -> .parent = Settings, .parent = config, .parent = project root
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load .env from project root
load_dotenv(BASE_DIR / '.env')

APP_ENVIRONMENT = os.environ.get('APP_ENVIRONMENT', 'development').lower()
APP_NAME = os.environ.get('APP_NAME', 'Heritage 360')
APP_RELEASE = os.environ.get('RENDER_GIT_COMMIT', '')
OPERATIONS_DASHBOARD_URLS = {
    name: os.environ.get(f'OPERATIONS_{key}_URL', '')
    for name, key in (
        ('Render', 'RENDER'), ('Cloudflare', 'CLOUDFLARE'),
        ('Resend', 'RESEND'), ('Sentry', 'SENTRY'),
        ('Better Stack', 'UPTIME'), ('GitHub', 'GITHUB'),
    )
}


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {
        '1',
        'true',
        'yes',
        'on',
    }

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ['SECRET_KEY']

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'axes',
    'django_otp',
    'django_otp.plugins.otp_totp',
    # Local
    'users',
    'core',
    'projects',
    'integrations',
    'billing',
    'subscriptions',
]

AUTH_USER_MODEL = 'users.User'

AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    'django.contrib.auth.backends.ModelBackend',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'core.operations.OperationalFailureMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'users.mfa.AdministratorMFAMiddleware',
    'core.security.BrowserSecurityMiddleware',
    'projects.middleware.ClientViewAuditMiddleware',
    'axes.middleware.AxesMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# Axes — brute-force login protection
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1  # hours
AXES_LOCKOUT_CALLABLE = None  # uses default 403 response

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'projects.context_processors.workspace_navigation',
                'projects.context_processors.application_identity',
                'core.security.form_security',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = os.environ.get('TIME_ZONE', 'America/New_York')
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

# Media files
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'
PRIVATE_MEDIA_ROOT = Path(
    os.environ.get('PRIVATE_MEDIA_ROOT', BASE_DIR / 'private_media')
)
PRIVATE_STORAGE_BACKEND = os.environ.get('PRIVATE_STORAGE_BACKEND', 'local')
DOCUMENT_MAX_UPLOAD_SIZE = (
    int(os.environ.get('DOCUMENT_MAX_UPLOAD_SIZE_MB', '25')) * 1024 * 1024
)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Auth
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

# Enrolled accounts always require MFA. Administrators must enroll in production.
MFA_REQUIRED_FOR_ADMINS = env_bool('MFA_REQUIRED_FOR_ADMINS', APP_ENVIRONMENT == 'production')
MFA_SESSION_SECONDS = 8 * 60 * 60
MFA_SETUP_SECONDS = 10 * 60
OTP_TOTP_ISSUER = APP_NAME
OTP_TOTP_THROTTLE_FACTOR = 2
# Retain old application keys during a key rotation so encrypted authenticators
# can still be read. Never rotate SECRET_KEY without following the MFA runbook.
SECRET_KEY_FALLBACKS = [key.strip() for key in os.environ.get('SECRET_KEY_FALLBACKS', '').split(',') if key.strip()]

# Cache — overridden per environment
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}

# Logging. Containers should log to stdout; optional file logging is intended for
# local environments that explicitly enable it.
DJANGO_LOG_HANDLERS = ['console']
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': DJANGO_LOG_HANDLERS,
            'level': os.environ.get('DJANGO_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
    },
}

if env_bool('DJANGO_LOG_TO_FILE', False):
    django_log_directory = Path(
        os.environ.get('DJANGO_LOG_DIRECTORY', BASE_DIR / 'logs')
    )
    django_log_directory.mkdir(parents=True, exist_ok=True)
    LOGGING['handlers']['file'] = {
        'class': 'logging.FileHandler',
        'filename': django_log_directory / 'django.log',
        'formatter': 'verbose',
    }
    DJANGO_LOG_HANDLERS.append('file')

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ['DB_NAME'],
        'USER': os.environ['DB_USER'],
        'PASSWORD': os.environ['DB_PASSWORD'],
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '5432'),
    }
}

# Email
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'localhost')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', True)
EMAIL_USE_SSL = env_bool('EMAIL_USE_SSL', False)
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'webmaster@localhost')
DEFAULT_REPLY_TO_EMAIL = os.environ.get('DEFAULT_REPLY_TO_EMAIL', '')
EMAIL_TIMEOUT = int(os.environ.get('EMAIL_TIMEOUT', '15'))

# Base URL used to build absolute links in emails sent outside of a request
# context (e.g. management commands run by an external scheduler).
SITE_BASE_URL = os.environ.get('SITE_BASE_URL', 'http://localhost:8000')

# Public legal policy details. Set these to the operating legal entity's details
# before requesting production access from third-party platforms.
LEGAL_BUSINESS_NAME = os.environ.get('LEGAL_BUSINESS_NAME', 'Construction Manager')
LEGAL_CONTACT_EMAIL = os.environ.get('LEGAL_CONTACT_EMAIL', 'support@localhost')
SUPPORT_EMAIL = os.environ.get('SUPPORT_EMAIL', LEGAL_CONTACT_EMAIL)
LEGAL_BUSINESS_ADDRESS = os.environ.get('LEGAL_BUSINESS_ADDRESS', '')
LEGAL_GOVERNING_LAW = os.environ.get(
    'LEGAL_GOVERNING_LAW', 'applicable laws of the United States'
)
LEGAL_EFFECTIVE_DATE = os.environ.get('LEGAL_EFFECTIVE_DATE', 'July 31, 2026')

# Cloudflare Turnstile protects browser form submissions; public QR access is retired.
TURNSTILE_SITE_KEY = os.environ.get('TURNSTILE_SITE_KEY', '')
TURNSTILE_SECRET_KEY = os.environ.get('TURNSTILE_SECRET_KEY', '')
TURNSTILE_FORMS_ENABLED = env_bool('TURNSTILE_FORMS_ENABLED', APP_ENVIRONMENT == 'production')

# QuickBooks integration is hidden for the Heritage launch. Heritage uses
# QuickBooks Desktop, which requires a separate Web Connector/qbXML adapter.
QUICKBOOKS_INTEGRATION_ENABLED = env_bool('QUICKBOOKS_INTEGRATION_ENABLED', False)
QUICKBOOKS_DESKTOP_CONNECTOR_ENABLED = env_bool('QUICKBOOKS_DESKTOP_CONNECTOR_ENABLED', False)

# Dormant QuickBooks Online foundation. Sandbox credentials are development-only. Realm/company IDs
# are captured per organization during OAuth rather than configured globally.
QUICKBOOKS_ENVIRONMENT = os.environ.get('QUICKBOOKS_ENVIRONMENT', 'sandbox').lower()
QUICKBOOKS_CLIENT_ID = os.environ.get('QUICKBOOKS_CLIENT_ID', '')
QUICKBOOKS_CLIENT_SECRET = os.environ.get('QUICKBOOKS_CLIENT_SECRET', '')
QUICKBOOKS_REDIRECT_URI = os.environ.get('QUICKBOOKS_REDIRECT_URI', '')
QUICKBOOKS_TOKEN_ENCRYPTION_KEYS = tuple(
    key.strip()
    for key in os.environ.get('QUICKBOOKS_TOKEN_ENCRYPTION_KEYS', '').split(',')
    if key.strip()
)
QUICKBOOKS_WEBHOOK_VERIFIER_TOKEN = os.environ.get(
    'QUICKBOOKS_WEBHOOK_VERIFIER_TOKEN', ''
)
QUICKBOOKS_AUTHORIZATION_URL = 'https://appcenter.intuit.com/connect/oauth2'
QUICKBOOKS_TOKEN_URL = 'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer'
QUICKBOOKS_REVOKE_URL = 'https://developer.api.intuit.com/v2/oauth2/tokens/revoke'
QUICKBOOKS_SCOPES = ('com.intuit.quickbooks.accounting',)
QUICKBOOKS_HTTP_TIMEOUT_SECONDS = int(
    os.environ.get('QUICKBOOKS_HTTP_TIMEOUT_SECONDS', '15')
)
QUICKBOOKS_OAUTH_STATE_TTL_SECONDS = int(
    os.environ.get('QUICKBOOKS_OAUTH_STATE_TTL_SECONDS', '600')
)
QUICKBOOKS_MINOR_VERSION = int(os.environ.get('QUICKBOOKS_MINOR_VERSION', '75'))
QUICKBOOKS_SYNC_MAX_ATTEMPTS = int(
    os.environ.get('QUICKBOOKS_SYNC_MAX_ATTEMPTS', '5')
)
QUICKBOOKS_SYNC_RETRY_BASE_SECONDS = int(
    os.environ.get('QUICKBOOKS_SYNC_RETRY_BASE_SECONDS', '60')
)
QUICKBOOKS_CONFIGURED = all(
    (
        QUICKBOOKS_CLIENT_ID,
        QUICKBOOKS_CLIENT_SECRET,
        QUICKBOOKS_REDIRECT_URI,
        QUICKBOOKS_TOKEN_ENCRYPTION_KEYS,
    )
)

# Dedicated deployments do not use application access billing. This separate
# feature must be explicitly enabled by deployments that offer paid access.
SUBSCRIPTION_BILLING_ENABLED = env_bool('SUBSCRIPTION_BILLING_ENABLED', False)
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
STRIPE_MONTHLY_SUBSCRIPTION_PRICE_ID = os.environ.get(
    'STRIPE_MONTHLY_SUBSCRIPTION_PRICE_ID', ''
)
STRIPE_YEARLY_SUBSCRIPTION_PRICE_ID = os.environ.get(
    'STRIPE_YEARLY_SUBSCRIPTION_PRICE_ID', ''
)
STRIPE_SUBSCRIPTION_PRICE_IDS = {
    'standard_monthly': STRIPE_MONTHLY_SUBSCRIPTION_PRICE_ID,
    'standard_yearly': STRIPE_YEARLY_SUBSCRIPTION_PRICE_ID,
}
STRIPE_CHECKOUT_SESSION_MINUTES = int(
    os.environ.get('STRIPE_CHECKOUT_SESSION_MINUTES', '30')
)
STRIPE_PAST_DUE_GRACE_DAYS = int(
    os.environ.get('STRIPE_PAST_DUE_GRACE_DAYS', '7')
)
STRIPE_AUTOMATIC_TAX_ENABLED = env_bool('STRIPE_AUTOMATIC_TAX_ENABLED', False)
STRIPE_ENFORCE_SUBSCRIPTIONS = env_bool('STRIPE_ENFORCE_SUBSCRIPTIONS', False)
STRIPE_SUBSCRIPTIONS_CONFIGURED = all(
    (
        STRIPE_SECRET_KEY,
        STRIPE_WEBHOOK_SECRET,
        STRIPE_MONTHLY_SUBSCRIPTION_PRICE_ID,
        STRIPE_YEARLY_SUBSCRIPTION_PRICE_ID,
    )
)
STRIPE_LIVE_MODE = STRIPE_SECRET_KEY.startswith('sk_live_')
