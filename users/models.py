from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = 'user'
        verbose_name_plural = 'users'
        constraints = [
            models.UniqueConstraint(Lower('email'), name='users_email_ci_unique'),
        ]

    def clean(self):
        super().clean()
        self.email = self.__class__.objects.normalize_email(self.email).lower()

    def get_full_name(self):
        return f'{self.first_name} {self.last_name}'.strip()

    def get_short_name(self):
        return self.first_name

    def __str__(self):
        return self.email


class AuthenticatorDevice(TOTPDevice):
    """Use django-otp replay/throttling logic with an encrypted seed at rest."""

    account = models.OneToOneField(User, on_delete=models.CASCADE, related_name='authenticator')
    encrypted_key = models.TextField(editable=False)
    setup_started_at = models.DateTimeField(default=timezone.now)

    @property
    def bin_key(self):
        from .mfa_crypto import seed_cipher
        return seed_cipher().decrypt(self.encrypted_key.encode())

    def save(self, *args, **kwargs):
        import secrets

        from .mfa_crypto import seed_cipher
        if not self.encrypted_key:
            self.encrypted_key = seed_cipher().encrypt(secrets.token_bytes(20)).decode()
        self.user_id = self.account_id
        # The parent table must never contain the plaintext seed.
        self.key = ''
        super().save(*args, **kwargs)


class MFARecoveryCode(models.Model):
    device = models.ForeignKey(AuthenticatorDevice, on_delete=models.CASCADE, related_name='recovery_codes')
    digest = models.CharField(max_length=64, unique=True)


class MFAAuditEvent(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    event = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)
