from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase


class BootstrapSuperuserCommandTests(TestCase):
    credentials = {
        "DJANGO_SUPERUSER_EMAIL": "ADMIN@EXAMPLE.COM",
        "DJANGO_SUPERUSER_PASSWORD": "Strong-test-password-923!",
        "DJANGO_SUPERUSER_FIRST_NAME": "Site",
        "DJANGO_SUPERUSER_LAST_NAME": "Administrator",
    }

    def run_command(self, environment=None):
        stdout = StringIO()
        environment = self.credentials if environment is None else environment
        with patch.dict("os.environ", environment, clear=True):
            call_command("bootstrap_superuser", stdout=stdout)
        return stdout.getvalue()

    def test_creates_superuser_from_environment(self):
        output = self.run_command()

        user = get_user_model().objects.get(email="admin@example.com")
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.get_full_name(), "Site Administrator")
        self.assertTrue(user.check_password("Strong-test-password-923!"))
        self.assertIn("Superuser admin@example.com created", output)

    def test_is_idempotent_and_applies_a_changed_password(self):
        self.run_command()
        changed_environment = {
            **self.credentials,
            "DJANGO_SUPERUSER_PASSWORD": "Different-strong-password-417!",
        }

        output = self.run_command(changed_environment)

        self.assertEqual(get_user_model().objects.count(), 1)
        user = get_user_model().objects.get()
        self.assertTrue(user.check_password("Different-strong-password-417!"))
        self.assertIn("Superuser admin@example.com updated", output)

    def test_skips_when_credentials_are_not_configured(self):
        output = self.run_command({})

        self.assertFalse(get_user_model().objects.exists())
        self.assertIn("bootstrap skipped", output)

    def test_rejects_partial_credentials(self):
        with self.assertRaisesMessage(CommandError, "Set both"):
            self.run_command({"DJANGO_SUPERUSER_EMAIL": "admin@example.com"})
