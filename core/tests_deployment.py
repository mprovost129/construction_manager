import tempfile
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.test import SimpleTestCase, override_settings

from config.storage import r2_storage_config
from core.mail_backend import EmailBackend
from projects.storage import PrivateDocumentStorage


class PrivateStorageTests(SimpleTestCase):
    def test_local_storage_retains_files_and_never_exposes_a_url(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(
            PRIVATE_MEDIA_ROOT=directory, PRIVATE_STORAGE_BACKEND='local'
        ):
            storage = PrivateDocumentStorage()
            name = storage.save('job/document.txt', ContentFile(b'example'))
            self.assertEqual(storage.open(name).read(), b'example')
            self.assertEqual(storage.size(name), 7)
            self.assertTrue(storage.exists(name))
            with self.assertRaises(ValueError):
                storage.url(name)
            storage.delete(name)
            self.assertFalse(storage.exists(name))

    @override_settings(
        PRIVATE_STORAGE_BACKEND='r2',
        STORAGES={'private_documents': {
            'BACKEND': 'django.core.files.storage.InMemoryStorage',
        }},
    )
    def test_r2_routing_uses_configured_storage_and_keeps_versions(self):
        storage = PrivateDocumentStorage()
        first = storage.save('job/photo.jpg', ContentFile(b'first'))
        second = storage.save('job/photo.jpg', ContentFile(b'second'))
        self.assertNotEqual(first, second)
        self.assertEqual(storage.open(first).read(), b'first')
        self.assertEqual(storage.open(second).read(), b'second')
        with self.assertRaises(ValueError):
            storage.url(first)
        storage.delete(first)
        self.assertFalse(storage.exists(first))
        self.assertTrue(storage.exists(second))

    def test_missing_r2_configuration_fails_without_local_fallback(self):
        with self.assertRaises(ImproperlyConfigured):
            r2_storage_config({})

    def test_r2_configuration_uses_private_cloudflare_endpoint(self):
        config = r2_storage_config({
            'R2_ACCOUNT_ID': 'a' * 32,
            'R2_BUCKET_NAME': 'test-bucket',
            'R2_ACCESS_KEY_ID': 'test-only-access',
            'R2_SECRET_ACCESS_KEY': 'test-only-secret',
        })
        options = config['OPTIONS']
        self.assertEqual(options['endpoint_url'], f'https://{"a" * 32}.r2.cloudflarestorage.com')
        self.assertIsNone(options['default_acl'])
        self.assertIsNone(options['custom_domain'])
        self.assertTrue(options['querystring_auth'])
        self.assertFalse(options['file_overwrite'])
        self.assertEqual(options['object_parameters']['CacheControl'], 'private, no-store')


class ReplyAddressTests(SimpleTestCase):
    @override_settings(DEFAULT_REPLY_TO_EMAIL='support@example.com')
    @patch('django.core.mail.backends.smtp.EmailBackend.send_messages', return_value=2)
    def test_default_reply_address_preserves_explicit_replies(self, send):
        default = EmailMessage('Reset password', 'Example', to=['user@example.com'])
        explicit = EmailMessage(
            'Project message', 'Example', to=['user@example.com'],
            reply_to=['pm@example.com'],
        )
        self.assertEqual(EmailBackend().send_messages([default, explicit]), 2)
        self.assertEqual(default.reply_to, ['support@example.com'])
        self.assertEqual(explicit.reply_to, ['pm@example.com'])
