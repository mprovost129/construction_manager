"""Recover a snapshot into a new ZIP archive; never writes to live storage."""

import argparse
import io
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from ops.backup_documents import (
    CHUNK_SIZE,
    MAX_FILE_BYTES,
    MAX_OBJECTS,
    client,
    fingerprint,
)


def restore(storage, bucket, snapshot, output):
    output = Path(output)
    if output.exists():
        raise ValueError('Recovery destination must not exist.')
    if not re.fullmatch(r'snapshots/[A-Za-z0-9-]+\.json', snapshot):
        raise ValueError('Invalid snapshot identifier.')
    manifest_data = io.BytesIO()
    response = storage.get_object(Bucket=bucket, Key=snapshot)
    fingerprint(response['Body'], manifest_data, limit=32 * CHUNK_SIZE)
    manifest = json.loads(manifest_data.getvalue())
    entries = manifest['objects']
    if manifest.get('version') != 1 or not isinstance(entries, list) or not 0 < len(entries) <= MAX_OBJECTS:
        raise ValueError('Invalid manifest.')
    seen = set()
    for entry in entries:
        key = entry['source_key']
        path = PurePosixPath(key)
        if (not key or '\\' in key or ':' in key or '\x00' in key or path.is_absolute()
                or '..' in path.parts or str(path) != key or key in seen):
            raise ValueError('Unsafe or duplicate archive path.')
        seen.add(key)
        if (not re.fullmatch(r'[a-f0-9]{64}', entry['sha256'])
                or entry['backup_key'] != 'objects/' + entry['sha256']
                or type(entry['size']) is not int or not 0 <= entry['size'] <= MAX_FILE_BYTES):
            raise ValueError('Invalid object reference.')
    with tempfile.TemporaryDirectory(prefix='heritage-recovery-', dir=output.parent) as temporary:
        archive_path = Path(temporary) / 'recovery.zip'
        with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for entry in entries:
                response = storage.get_object(Bucket=bucket, Key=entry['backup_key'])
                with archive.open(entry['source_key'], 'w') as recovered:
                    result = fingerprint(response['Body'], recovered)
                if result != (entry['sha256'], entry['size']):
                    raise ValueError('Recovered content verification failed.')
        # Atomic publication on the same filesystem, failing if the target exists.
        os.link(archive_path, output)
    return len(entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        count = restore(client(os.environ, 'BACKUP'), os.environ['BACKUP_BUCKET'], args.snapshot, args.output)
    except Exception:
        print('Document recovery failed. No live storage was changed.', file=sys.stderr)
        return 1
    print(json.dumps({'status': 'recovered', 'objects': count}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
