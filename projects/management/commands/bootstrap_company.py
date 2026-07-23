import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from projects.models import Organization, OrganizationMembership, Project


class Command(BaseCommand):
    help = 'Create the initial company, application admin, and optional project.'

    def add_arguments(self, parser):
        parser.add_argument('--company-name', required=True)
        parser.add_argument('--admin-email', required=True)
        parser.add_argument('--project-name', default='Sample Project')
        parser.add_argument(
            '--password-env',
            default='BOOTSTRAP_ADMIN_PASSWORD',
            help='Environment variable containing the initial admin password.',
        )
        parser.add_argument(
            '--skip-project',
            action='store_true',
            help='Create only the company and admin membership.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        company_name = options['company_name'].strip()
        admin_email = options['admin_email'].strip().lower()
        project_name = options['project_name'].strip()

        if not company_name:
            raise CommandError('Company name cannot be blank.')
        if not admin_email:
            raise CommandError('Admin email cannot be blank.')
        if not options['skip_project'] and not project_name:
            raise CommandError('Project name cannot be blank.')

        slug = slugify(company_name)
        if not slug:
            raise CommandError('Company name must contain letters or numbers.')

        organization, organization_created = Organization.objects.get_or_create(
            slug=slug,
            defaults={'name': company_name},
        )
        if not organization_created and organization.name != company_name:
            raise CommandError(
                f'The slug "{slug}" already belongs to "{organization.name}".'
            )

        user_model = get_user_model()
        user = user_model.objects.filter(email__iexact=admin_email).first()
        user_created = user is None
        password_configured = False
        if user_created:
            user = user_model(email=admin_email, is_active=True)
            password = os.environ.get(options['password_env'])
            if password:
                try:
                    validate_password(password, user=user)
                except ValidationError as error:
                    raise CommandError('; '.join(error.messages)) from error
                user.set_password(password)
                password_configured = True
            else:
                user.set_unusable_password()
            user.full_clean()
            user.save()

        membership, membership_created = OrganizationMembership.objects.get_or_create(
            organization=organization,
            user=user,
            defaults={'role': OrganizationMembership.Role.ADMIN},
        )
        if not membership_created and membership.role != OrganizationMembership.Role.ADMIN:
            raise CommandError(
                f'{user.email} already has the {membership.get_role_display()} role '
                f'at {organization.name}; no role was changed.'
            )

        project = None
        project_created = False
        if not options['skip_project']:
            project, project_created = Project.objects.get_or_create(
                organization=organization,
                name=project_name,
                defaults={
                    'status': Project.Status.ACTIVE,
                    'created_by': user,
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'Company: {organization.name} '
                f'({"created" if organization_created else "existing"})'
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'Application admin: {user.email} '
                f'({"created" if user_created else "existing"})'
            )
        )
        if project:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Project: {project.name} '
                    f'({"created" if project_created else "existing"})'
                )
            )
        if user_created and not password_configured:
            self.stdout.write(
                self.style.WARNING(
                    f'No {options["password_env"]} value was provided. The admin '
                    'has an unusable password; use the password-reset flow before login.'
                )
            )
