"""Independent, append-only R2 backups. Never imports Django or writes to source."""

import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from contextlib import closing
from datetime import UTC, datetime

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

CHUNK_SIZE = 1024 * 1024
MAX_FILE_BYTES = 250 * CHUNK_SIZE
MAX_OBJECTS = 100_000


def fingerprint(body, output=None, limit=MAX_FILE_BYTES):
    digest = hashlib.sha256()
    size = 0
    with closing(body):
        while chunk := body.read(CHUNK_SIZE):
            size += len(chunk)
            if size > limit:
                raise ValueError('Backup size limit exceeded.')
            digest.update(chunk)
            if output is not None:
                output.write(chunk)
    return digest.hexdigest(), size


def backup(source, destination, source_bucket, backup_bucket):
    if source_bucket == backup_bucket:
        raise ValueError('Source and backup buckets must differ.')
    started = datetime.now(UTC).isoformat()
    inventory = []
    copied = 0
    for page in source.get_paginator('list_objects_v2').paginate(Bucket=source_bucket):
        for entry in page.get('Contents', []):
            if len(inventory) >= MAX_OBJECTS or entry['Size'] > MAX_FILE_BYTES:
                raise ValueError('Backup limit exceeded; review capacity.')
            with tempfile.TemporaryFile() as temporary:
                response = source.get_object(
                    Bucket=source_bucket, Key=entry['Key'], IfMatch=entry['ETag'],
                )
                digest, size = fingerprint(response['Body'], temporary)
                if size != entry['Size']:
                    raise ValueError('Source changed during backup.')
                key = 'objects/' + digest
                try:
                    existing = destination.head_object(Bucket=backup_bucket, Key=key)
                except ClientError as exc:
                    if exc.response.get('Error', {}).get('Code') not in ('404', 'NoSuchKey'):
                        raise
                    existing = None
                if existing is None:
                    temporary.seek(0)
                    destination.put_object(
                        Bucket=backup_bucket, Key=key, Body=temporary,
                        ContentLength=size, Metadata={'sha256': digest},
                        ContentType='application/octet-stream', CacheControl='private, no-store',
                        IfNoneMatch='*',
                    )
                    copied += 1
                elif existing['ContentLength'] != size:
                    raise ValueError('Existing backup size mismatch.')
                # Read back every referenced object, including older deduplicated copies.
                verified = destination.get_object(Bucket=backup_bucket, Key=key)
                if fingerprint(verified['Body']) != (digest, size):
                    raise ValueError('Backup content verification failed.')
                inventory.append({
                    'source_key': entry['Key'], 'backup_key': key,
                    'sha256': digest, 'size': size,
                })
    if not inventory:
        raise ValueError('Empty source requires investigation; no success snapshot recorded.')
    snapshot = 'snapshots/' + datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex + '.json'
    manifest = json.dumps({
        'version': 1, 'started_at': started, 'completed_at': datetime.now(UTC).isoformat(),
        'source_bucket': source_bucket, 'objects': inventory,
    }, sort_keys=True).encode()
    if len(manifest) > 32 * CHUNK_SIZE:
        raise ValueError('Manifest exceeds backup limit.')
    destination.put_object(
        Bucket=backup_bucket, Key=snapshot, Body=manifest,
        ContentType='application/json', CacheControl='private, no-store', IfNoneMatch='*',
    )
    saved = destination.get_object(Bucket=backup_bucket, Key=snapshot)
    if fingerprint(saved['Body'], limit=32 * CHUNK_SIZE) != (hashlib.sha256(manifest).hexdigest(), len(manifest)):
        raise ValueError('Manifest readback failed.')
    return {'status': 'verified', 'objects': len(inventory), 'new_copies': copied, 'snapshot': snapshot}


def client(environment, prefix):
    account = environment['R2_ACCOUNT_ID'].strip()
    if not re.fullmatch(r'[a-fA-F0-9]{32}', account):
        raise ValueError('Invalid account identifier.')
    access = environment[prefix + '_ACCESS_KEY_ID'].strip()
    secret = environment[prefix + '_SECRET_ACCESS_KEY'].strip()
    if not access or not secret:
        raise ValueError('Missing scoped storage credentials.')
    return boto3.client(
        's3', endpoint_url=f'https://{account}.r2.cloudflarestorage.com',
        region_name='auto', aws_access_key_id=access, aws_secret_access_key=secret,
        config=Config(
            signature_version='s3v4', connect_timeout=10, read_timeout=60,
            retries={'max_attempts': 3, 'mode': 'standard'},
            s3={'addressing_style': 'path'},
        ),
    )


def main():
    try:
        result = backup(
            client(os.environ, 'SOURCE'), client(os.environ, 'BACKUP'),
            os.environ['SOURCE_BUCKET'], os.environ['BACKUP_BUCKET'],
        )
    except Exception:
        # Provider errors can contain tokens or private object names. Render alerts on exit 1.
        print('Document backup failed; no successful run confirmed. Check credentials, limits, and storage.', file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
