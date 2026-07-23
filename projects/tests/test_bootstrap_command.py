from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from projects.models import Organization, OrganizationMembership, Project


class BootstrapCompanyCommandTests(TestCase):
    def run_command(self, **overrides):
        options = {
            'company_name': 'Example Builders',
            'admin_email': 'ADMIN@EXAMPLE.COM',
            'project_name': 'Oak Street',
            'stdout': StringIO(),
        }
        options.update(overrides)
        with patch.dict(
            'os.environ',
            {'BOOTSTRAP_ADMIN_PASSWORD': 'Strong-test-password-923!'},
        ):
            call_command('bootstrap_company', **options)
        return options['stdout'].getvalue()

    def test_creates_company_admin_membership_and_project(self):
        output = self.run_command()

        organization = Organization.objects.get(slug='example-builders')
        user = get_user_model().objects.get(email='admin@example.com')
        membership = OrganizationMembership.objects.get(
            organization=organization,
            user=user,
        )
        project = Project.objects.get(organization=organization, name='Oak Street')

        self.assertEqual(membership.role, OrganizationMembership.Role.ADMIN)
        self.assertTrue(user.check_password('Strong-test-password-923!'))
        self.assertEqual(project.created_by, user)
        self.assertEqual(project.status, Project.Status.ACTIVE)
        self.assertIn('Company: Example Builders (created)', output)

    def test_is_idempotent(self):
        self.run_command()
        output = self.run_command()

        self.assertEqual(Organization.objects.count(), 1)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(OrganizationMembership.objects.count(), 1)
        self.assertEqual(Project.objects.count(), 1)
        self.assertIn('Company: Example Builders (existing)', output)
        self.assertIn('Application admin: admin@example.com (existing)', output)
        self.assertIn('Project: Oak Street (existing)', output)

    def test_can_create_admin_without_a_sample_project(self):
        self.run_command(skip_project=True)

        self.assertEqual(Organization.objects.count(), 1)
        self.assertEqual(OrganizationMembership.objects.count(), 1)
        self.assertFalse(Project.objects.exists())

    def test_missing_password_creates_unusable_password(self):
        output_stream = StringIO()
        with patch.dict('os.environ', {}, clear=True):
            call_command(
                'bootstrap_company',
                company_name='Example Builders',
                admin_email='admin@example.com',
                skip_project=True,
                stdout=output_stream,
            )

        user = get_user_model().objects.get(email='admin@example.com')
        self.assertFalse(user.has_usable_password())
        self.assertIn('has an unusable password', output_stream.getvalue())
