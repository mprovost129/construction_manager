"""Explicit private Cloudflare R2 configuration without AWS credential fallback."""

import re

from django.core.exceptions import ImproperlyConfigured


def r2_storage_config(environment):
    names = ('R2_ACCOUNT_ID', 'R2_BUCKET_NAME', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY')
    if any(not environment.get(name, '').strip() for name in names):
        raise ImproperlyConfigured('Private R2 storage requires all R2 settings.')
    account_id = environment['R2_ACCOUNT_ID'].strip()
    if not re.fullmatch(r'[a-fA-F0-9]{32}', account_id):
        raise ImproperlyConfigured('Invalid R2 account identifier.')
    return {
        'BACKEND': 'storages.backends.s3.S3Storage',
        'OPTIONS': {
            'endpoint_url': f'https://{account_id}.r2.cloudflarestorage.com',
            'bucket_name': environment['R2_BUCKET_NAME'].strip(),
            'access_key': environment['R2_ACCESS_KEY_ID'],
            'secret_key': environment['R2_SECRET_ACCESS_KEY'],
            'region_name': 'auto',
            'signature_version': 's3v4',
            'addressing_style': 'path',
            'default_acl': None,
            'custom_domain': None,
            'querystring_auth': True,
            'file_overwrite': False,
            'max_memory_size': 2 * 1024 * 1024,
            'object_parameters': {'CacheControl': 'private, no-store'},
        },
    }
