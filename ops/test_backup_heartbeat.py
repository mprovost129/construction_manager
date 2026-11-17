import io
import unittest
from unittest.mock import Mock, patch

from ops.backup_documents import main, send_heartbeat


class HeartbeatTests(unittest.TestCase):
    url = 'https://incidents.betterstack.com/api/v1/heartbeat/test-placeholder'

    @patch('ops.backup_documents.http.client.HTTPSConnection')
    def test_completion_request_contains_no_backup_data(self, connection):
        connection.return_value.getresponse.return_value.status = 204
        send_heartbeat(self.url)
        connection.assert_called_once_with('incidents.betterstack.com', timeout=10)
        connection.return_value.request.assert_called_once_with('GET', '/api/v1/heartbeat/test-placeholder')
        connection.return_value.close.assert_called_once()

    @patch('ops.backup_documents.http.client.HTTPSConnection')
    def test_invalid_destinations_fail_before_network_access(self, connection):
        for url in (
            'http://incidents.betterstack.com/api/v1/heartbeat/test-placeholder',
            self.url + '?private=data', self.url + '#fragment', self.url + '/fail',
            'https://incidents.betterstack.com.attacker.test/api/v1/heartbeat/test-placeholder',
            'https://user:password@incidents.betterstack.com/api/v1/heartbeat/test-placeholder',
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                send_heartbeat(url)
        connection.assert_not_called()

    @patch('ops.backup_documents.http.client.HTTPSConnection')
    def test_redirects_are_not_followed(self, connection):
        connection.return_value.getresponse.return_value.status = 302
        with self.assertRaises(RuntimeError):
            send_heartbeat(self.url)
        self.assertEqual(connection.call_count, 1)
        connection.return_value.close.assert_called_once()

    @patch('ops.backup_documents.http.client.HTTPSConnection')
    def test_transient_failure_retries_and_closes_connections(self, connection):
        connection.return_value.getresponse.side_effect = [TimeoutError(), Mock(status=503), Mock(status=200)]
        send_heartbeat(self.url)
        self.assertEqual(connection.call_count, 3)
        self.assertEqual(connection.return_value.close.call_count, 3)

    @patch('ops.backup_documents.http.client.HTTPSConnection')
    def test_exhausted_retries_fail_safely(self, connection):
        connection.return_value.request.side_effect = OSError('private URL details')
        with self.assertRaisesRegex(RuntimeError, '^Backup completion notification failed.$'):
            send_heartbeat(self.url)
        self.assertEqual(connection.call_count, 3)
        self.assertEqual(connection.return_value.close.call_count, 3)

    @patch.dict('os.environ', {'SOURCE_BUCKET': 'source', 'BACKUP_BUCKET': 'backup', 'BACKUP_HEARTBEAT_URL': url}, clear=True)
    def test_heartbeat_only_follows_verified_backup(self):
        order = []
        with patch('ops.backup_documents.client'), patch('sys.stdout', io.StringIO()), patch('sys.stderr', io.StringIO()):
            with patch('ops.backup_documents.backup', side_effect=lambda *args: order.append('verified') or {'status': 'verified'}):
                with patch('ops.backup_documents.send_heartbeat', side_effect=lambda url: order.append('heartbeat')):
                    self.assertEqual(main(), 0)
            self.assertEqual(order, ['verified', 'heartbeat'])
            with patch('ops.backup_documents.backup', side_effect=RuntimeError('failed readback')):
                with patch('ops.backup_documents.send_heartbeat') as heartbeat:
                    self.assertEqual(main(), 1)
                    heartbeat.assert_not_called()

    @patch.dict('os.environ', {'SOURCE_BUCKET': 'source', 'BACKUP_BUCKET': 'backup', 'BACKUP_HEARTBEAT_URL': url}, clear=True)
    def test_notification_failure_keeps_backup_but_fails_job_without_secret_output(self):
        stderr = io.StringIO()
        with patch('ops.backup_documents.client'), patch('ops.backup_documents.backup', return_value={'status': 'verified'}):
            with patch('ops.backup_documents.send_heartbeat', side_effect=RuntimeError(self.url)):
                with patch('sys.stderr', stderr), patch('sys.stdout', io.StringIO()):
                    self.assertEqual(main(), 1)
        self.assertIn('Backup verified, but completion monitoring failed.', stderr.getvalue())
        self.assertNotIn(self.url, stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
