from django.core.management.base import BaseCommand
from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from users.mfa_crypto import seed_cipher
from users.models import AuthenticatorDevice


class Command(BaseCommand):
    help = 'Re-encrypt authenticator seeds with the current application key; no seeds are printed.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Apply changes; otherwise only verify all seeds can be read.')

    @sensitive_variables()
    def handle(self, *args, **options):
        count = 0
        with transaction.atomic():
            cipher = seed_cipher()
            for device in AuthenticatorDevice.objects.select_for_update().order_by('pk'):
                encrypted = cipher.rotate(device.encrypted_key.encode()).decode()
                if options['apply']:
                    device.encrypted_key = encrypted
                    device.save(update_fields=['encrypted_key'])
                count += 1
        self.stdout.write(f'{count} authenticator(s) {"rewrapped" if options["apply"] else "verified; no changes applied"}.')
