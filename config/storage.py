"""Explicit private Cloudflare R2 configuration without AWS credential fallback."""

import re

from boto3.s3.transfer import TransferConfig
from botocore.config import Config
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
            # Request threads already provide concurrency. Avoid a nested pool
            # and large download queues for every simultaneous image request.
            'transfer_config': TransferConfig(
                use_threads=False, max_io_queue=8, num_download_attempts=2,
            ),
            # Socket timeouts and limited retries bound stalled storage calls.
            # These are per-operation limits, not a total file-transfer deadline.
            'client_config': Config(
                signature_version='s3v4',
                s3={'addressing_style': 'path'},
                connect_timeout=3,
                read_timeout=15,
                retries={'mode': 'standard', 'total_max_attempts': 2},
                max_pool_connections=8,
            ),
            'object_parameters': {'CacheControl': 'private, no-store'},
        },
    }
