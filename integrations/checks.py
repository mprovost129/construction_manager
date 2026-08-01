from cryptography.fernet import Fernet
from django.conf import settings
from django.core.checks import Error, Warning, register


@register()
def quickbooks_configuration_check(app_configs, **kwargs):
    messages = []
    if settings.APP_ENVIRONMENT not in {'development', 'production'}:
        messages.append(
            Error(
                'APP_ENVIRONMENT must be development or production.',
                id='integrations.E004',
            )
        )
    environment = settings.QUICKBOOKS_ENVIRONMENT
    if environment not in {'sandbox', 'production'}:
        messages.append(
            Error(
                'QUICKBOOKS_ENVIRONMENT must be sandbox or production.',
                id='integrations.E001',
            )
        )

    credential_values = (
        settings.QUICKBOOKS_CLIENT_ID,
        settings.QUICKBOOKS_CLIENT_SECRET,
        settings.QUICKBOOKS_REDIRECT_URI,
        settings.QUICKBOOKS_TOKEN_ENCRYPTION_KEYS,
    )
    if any(credential_values) and not all(credential_values):
        messages.append(
            Warning(
                'QuickBooks configuration is incomplete; connections are disabled.',
                id='integrations.W001',
            )
        )

    for key in settings.QUICKBOOKS_TOKEN_ENCRYPTION_KEYS:
        try:
            Fernet(key.encode())
        except (AttributeError, TypeError, ValueError):
            messages.append(
                Error(
                    'QUICKBOOKS_TOKEN_ENCRYPTION_KEYS contains an invalid Fernet key.',
                    id='integrations.E003',
                )
            )
            break

    if (
        settings.APP_ENVIRONMENT == 'production'
        and settings.QUICKBOOKS_CONFIGURED
        and environment != 'production'
    ):
        messages.append(
            Error(
                'QuickBooks sandbox credentials cannot run in a production environment.',
                id='integrations.E002',
            )
        )
    return messages
