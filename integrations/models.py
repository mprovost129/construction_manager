from django.conf import settings
from django.db import models
from django.utils import timezone

from projects.models import Organization

from .crypto import decrypt_token, encrypt_token


class QuickBooksConnection(models.Model):
    class Environment(models.TextChoices):
        SANDBOX = 'sandbox', 'Sandbox'
        PRODUCTION = 'production', 'Production'

    class Status(models.TextChoices):
        CONNECTED = 'connected', 'Connected'
        DISCONNECTED = 'disconnected', 'Disconnected'
        REAUTHORIZATION_REQUIRED = (
            'reauthorization_required',
            'Reauthorization required',
        )
        ERROR = 'error', 'Connection error'

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='quickbooks_connections',
    )
    realm_id = models.CharField(max_length=50)
    environment = models.CharField(max_length=12, choices=Environment.choices)
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.CONNECTED,
    )
    scopes = models.JSONField(default=list, blank=True)
    encrypted_access_token = models.TextField(blank=True, editable=False)
    encrypted_refresh_token = models.TextField(blank=True, editable=False)
    access_token_expires_at = models.DateTimeField(null=True, blank=True)
    refresh_token_expires_at = models.DateTimeField(null=True, blank=True)
    connected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='quickbooks_connections_created',
    )
    connected_at = models.DateTimeField(default=timezone.now)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    last_refreshed_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=80, blank=True)
    last_error_message = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('organization__name', 'realm_id')
        constraints = [
            models.UniqueConstraint(
                fields=('environment', 'realm_id'),
                name='integrations_unique_quickbooks_realm',
            ),
        ]

    def __str__(self):
        return f'{self.organization} QuickBooks company {self.realm_id}'

    @property
    def access_token(self):
        return decrypt_token(self.encrypted_access_token)

    @property
    def refresh_token(self):
        return decrypt_token(self.encrypted_refresh_token)

    def set_tokens(self, *, access_token, refresh_token):
        self.encrypted_access_token = encrypt_token(access_token)
        self.encrypted_refresh_token = encrypt_token(refresh_token)

    def clear_error(self):
        self.last_error_code = ''
        self.last_error_message = ''

    def mark_disconnected(self):
        self.status = self.Status.DISCONNECTED
        self.encrypted_access_token = ''
        self.encrypted_refresh_token = ''
        self.access_token_expires_at = None
        self.refresh_token_expires_at = None
        self.disconnected_at = timezone.now()
        self.clear_error()
