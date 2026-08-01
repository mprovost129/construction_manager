from .base import *  # noqa: F403

DEBUG = False

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

AUTH_PASSWORD_VALIDATORS = []
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# Unit tests explicitly opt in to QuickBooks credentials where required.
QUICKBOOKS_CLIENT_ID = ''
QUICKBOOKS_CLIENT_SECRET = ''
QUICKBOOKS_REDIRECT_URI = ''
QUICKBOOKS_TOKEN_ENCRYPTION_KEYS = ()
QUICKBOOKS_CONFIGURED = False

STRIPE_SECRET_KEY = ''
STRIPE_WEBHOOK_SECRET = ''
STRIPE_MONTHLY_SUBSCRIPTION_PRICE_ID = ''
STRIPE_YEARLY_SUBSCRIPTION_PRICE_ID = ''
STRIPE_SUBSCRIPTION_PRICE_IDS = {
    'standard_monthly': '',
    'standard_yearly': '',
}
STRIPE_SUBSCRIPTIONS_CONFIGURED = False
STRIPE_LIVE_MODE = False
STRIPE_ENFORCE_SUBSCRIPTIONS = False
STRIPE_AUTOMATIC_TAX_ENABLED = False
