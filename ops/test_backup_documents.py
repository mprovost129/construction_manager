import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError

from ops.backup_documents import backup, main
from ops.restore_documents import restore


class Store:
    def __init__(self, objects=None):
        self.objects = objects or {}
        self.writes = []
        self.fail_key = None
        self.corrupt_reads = False
        self.changed = False

    def get_paginator(self, name):
        return self

    def paginate(self, **kwargs):
        # Separate pages exercise continuation handling.
        for key, value in self.objects.items():
            yield {'Contents': [{'Key': key, 'Size': len(value), 'ETag': 'original'}]}

    def get_object(self, Key, **kwargs):
        if self.changed and kwargs.get('IfMatch'):
            raise ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'GetObject')
        body = self.objects[Key]
        if self.corrupt_reads:
            body = b'corrupt'
        return {'Body': io.BytesIO(body)}

    def head_object(self, Key, **kwargs):
        if Key not in self.objects:
            raise ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        return {'ContentLength': len(self.objects[Key])}

    def put_object(self, Key, Body, **kwargs):
        if self.fail_key and Key.startswith(self.fail_key):
            raise RuntimeError('private provider detail')
        if Key in self.objects and kwargs.get('IfNoneMatch') == '*':
            raise RuntimeError('Object must not be overwritten')
        self.writes.append(Key)
        self.objects[Key] = Body.read() if hasattr(Body, 'read') else Body


class BackupTests(unittest.TestCase):
    def test_round_trip_and_deduplication_preserve_removed_source_files(self):
        source = Store({'plans/a.txt': b'first', 'photos/b.txt': b'second'})
        dest = Store()
        result = backup(source, dest, 'source', 'backup')
        manifest = json.loads(dest.objects[result['snapshot']])
        recovered = {entry['source_key']: dest.objects[entry['backup_key']] for entry in manifest['objects']}
        self.assertEqual(recovered, source.objects)
        self.assertEqual(result['objects'], 2)
        self.assertEqual(result['new_copies'], 2)
        self.assertEqual(source.writes, [])
        source.objects = {'plans/a.txt': b'revised'}
        second = backup(source, dest, 'source', 'backup')
        third = backup(source, dest, 'source', 'backup')
        self.assertEqual(second['new_copies'], 1)
        self.assertEqual(third['new_copies'], 0)
        self.assertEqual({entry['source_key']: dest.objects[entry['backup_key']] for entry in manifest['objects']}, recovered)

    def test_failed_upload_does_not_publish_snapshot(self):
        source, dest = Store({'a': b'private'}), Store()
        dest.fail_key = 'objects/'
        with self.assertRaises(RuntimeError):
            backup(source, dest, 'source', 'backup')
        self.assertFalse(any(key.startswith('snapshots/') for key in dest.objects))

    def test_corrupt_readback_does_not_publish_snapshot(self):
        dest = Store()
        dest.corrupt_reads = True
        with self.assertRaises(ValueError):
            backup(Store({'a': b'expected'}), dest, 'source', 'backup')
        self.assertFalse(any(key.startswith('snapshots/') for key in dest.objects))

    def test_old_backup_corruption_is_detected(self):
        value = b'expected'
        key = 'objects/' + hashlib.sha256(value).hexdigest()
        dest = Store({key: b'corrupt!'})
        with self.assertRaises(ValueError):
            backup(Store({'a': value}), dest, 'source', 'backup')
        self.assertEqual(dest.writes, [])

    def test_source_change_aborts_without_snapshot(self):
        source, dest = Store({'a': b'private'}), Store()
        source.changed = True
        with self.assertRaises(ClientError):
            backup(source, dest, 'source', 'backup')
        self.assertEqual(dest.writes, [])

    def test_empty_source_and_same_bucket_fail_closed(self):
        for source, destination in [('source', 'backup'), ('same', 'same')]:
            with self.assertRaises(ValueError):
                backup(Store(), Store(), source, destination)

    def test_permission_error_is_not_treated_as_missing_object(self):
        dest = Store()
        with patch.object(dest, 'head_object', side_effect=ClientError({'Error': {'Code': 'AccessDenied'}}, 'HeadObject')):
            with self.assertRaises(ClientError):
                backup(Store({'a': b'data'}), dest, 'source', 'backup')
        self.assertEqual(dest.writes, [])

    def test_cli_failure_hides_provider_details_and_returns_failure(self):
        stderr = io.StringIO()
        with patch('ops.backup_documents.client', side_effect=RuntimeError('private credential')), patch('sys.stderr', stderr):
            self.assertEqual(main(), 1)
        self.assertIn('Document backup failed', stderr.getvalue())
        self.assertNotIn('private credential', stderr.getvalue())

    def test_recovery_round_trip_never_overwrites(self):
        source, dest = Store({'plans/a.txt': b'document', 'photo.jpg': b'photo'}), Store()
        result = backup(source, dest, 'source', 'backup')
        writes = list(dest.writes)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'recovered.zip'
            self.assertEqual(restore(dest, 'backup', result['snapshot'], output), 2)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual({name: archive.read(name) for name in archive.namelist()}, source.objects)
            with self.assertRaises(ValueError):
                restore(dest, 'backup', result['snapshot'], output)
        self.assertEqual(dest.writes, writes)

    def test_recovery_rejects_traversal_and_corruption_without_publishing(self):
        for failure in ('path', 'content'):
            dest = Store()
            result = backup(Store({'a': b'data'}), dest, 'source', 'backup')
            manifest = json.loads(dest.objects[result['snapshot']])
            if failure == 'path':
                manifest['objects'][0]['source_key'] = '../outside'
                dest.objects[result['snapshot']] = json.dumps(manifest).encode()
            else:
                dest.objects[manifest['objects'][0]['backup_key']] = b'bad!'
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'recovered.zip'
                with self.assertRaises(ValueError):
                    restore(dest, 'backup', result['snapshot'], output)
                self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
