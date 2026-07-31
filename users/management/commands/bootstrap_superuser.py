import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Create or update a Django superuser from environment variables."

    @transaction.atomic
    def handle(self, *args, **options):
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip().lower()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")
        first_name = os.environ.get("DJANGO_SUPERUSER_FIRST_NAME")
        last_name = os.environ.get("DJANGO_SUPERUSER_LAST_NAME")

        if not email and not password:
            self.stdout.write(
                "Superuser bootstrap skipped; credential variables are not set."
            )
            return
        if not email or not password:
            raise CommandError(
                "Set both DJANGO_SUPERUSER_EMAIL and "
                "DJANGO_SUPERUSER_PASSWORD, or leave both unset."
            )

        user_model = get_user_model()
        user = user_model.objects.filter(email__iexact=email).first()
        created = user is None
        if created:
            user = user_model(email=email)

        if first_name is not None:
            user.first_name = first_name.strip()
        if last_name is not None:
            user.last_name = last_name.strip()
        user.email = email
        user.is_active = True
        user.is_staff = True
        user.is_superuser = True

        try:
            validate_password(password, user=user)
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from error

        password_changed = not user.check_password(password)
        if password_changed:
            user.set_password(password)
        try:
            user.full_clean()
        except ValidationError as error:
            messages = []
            for field_messages in error.message_dict.values():
                messages.extend(field_messages)
            raise CommandError("; ".join(messages)) from error
        user.save()

        action = "created" if created else "updated"
        password_status = "password set" if password_changed else "password unchanged"
        self.stdout.write(
            self.style.SUCCESS(f"Superuser {user.email} {action} ({password_status}).")
        )
