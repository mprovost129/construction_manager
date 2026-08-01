from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class TokenDecryptionError(Exception):
    """Raised when a stored credential cannot be decrypted."""


def _token_cipher():
    keys = getattr(settings, 'QUICKBOOKS_TOKEN_ENCRYPTION_KEYS', ())
    if isinstance(keys, str):
        keys = tuple(key.strip() for key in keys.split(',') if key.strip())
    if not keys:
        raise ImproperlyConfigured(
            'QUICKBOOKS_TOKEN_ENCRYPTION_KEYS must contain at least one Fernet key.'
        )
    try:
        return MultiFernet([Fernet(key.encode()) for key in keys])
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured(
            'QUICKBOOKS_TOKEN_ENCRYPTION_KEYS contains an invalid Fernet key.'
        ) from exc


def encrypt_token(value):
    if not value:
        return ''
    return _token_cipher().encrypt(value.encode()).decode()


def decrypt_token(value):
    if not value:
        return ''
    try:
        return _token_cipher().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise TokenDecryptionError(
            'The stored QuickBooks credential could not be decrypted.'
        ) from exc
