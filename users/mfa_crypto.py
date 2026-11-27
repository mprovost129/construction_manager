"""Encrypt authenticator seeds separately from the database credentials."""

import base64
import hmac

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.views.decorators.debug import sensitive_variables


@sensitive_variables()
def seed_cipher():
    # Purpose-separated key, with Django's rotation fallback keys supported.
    return MultiFernet([
        Fernet(base64.urlsafe_b64encode(hmac.digest(
            key.encode(), b'heritage360:authenticator-seed:v1', 'sha256',
        )))
        for key in [settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS]
    ])
