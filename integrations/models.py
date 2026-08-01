import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from projects.models import Organization, Project

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
    company_name = models.CharField(max_length=255, blank=True)
    legal_name = models.CharField(max_length=255, blank=True)
    country = models.CharField(max_length=10, blank=True)
    subscription_status = models.CharField(max_length=30, blank=True)
    offering_sku = models.CharField(max_length=100, blank=True)
    capabilities = models.JSONField(default=dict, blank=True)
    capabilities_checked_at = models.DateTimeField(null=True, blank=True)
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
        return f'{self.organization}: {self.display_name} ({self.get_environment_display()})'

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

    @property
    def display_name(self):
        return self.company_name or f'QuickBooks company {self.realm_id}'

    @property
    def capabilities_are_stale(self):
        return self.capabilities_checked_at is None


class QuickBooksProjectCustomerMapping(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        UNLINKED = 'unlinked', 'Unlinked'
        TOMBSTONED = 'tombstoned', 'Removed in QuickBooks'

    class OwnershipPolicy(models.TextChoices):
        QUICKBOOKS_AUTHORITATIVE = (
            'quickbooks_authoritative',
            'QuickBooks is authoritative',
        )

    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name='quickbooks_customer_mapping',
    )
    connection = models.ForeignKey(
        QuickBooksConnection,
        on_delete=models.CASCADE,
        related_name='project_customer_mappings',
    )
    quickbooks_customer_id = models.CharField(max_length=50)
    quickbooks_sync_token = models.CharField(max_length=50, blank=True)
    quickbooks_display_name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    external_active = models.BooleanField(default=True)
    ownership_policy = models.CharField(
        max_length=40,
        choices=OwnershipPolicy.choices,
        default=OwnershipPolicy.QUICKBOOKS_AUTHORITATIVE,
    )
    conflict_policy = models.CharField(
        max_length=40,
        default='quickbooks_wins',
        editable=False,
    )
    last_synced_values = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    tombstoned_at = models.DateTimeField(null=True, blank=True)
    unlinked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('project__name',)
        constraints = [
            models.UniqueConstraint(
                fields=('connection', 'quickbooks_customer_id'),
                condition=models.Q(status='active'),
                name='integrations_unique_active_project_customer',
            ),
        ]

    def __str__(self):
        return f'{self.project} -> {self.quickbooks_display_name}'

    def mark_tombstoned(self):
        self.status = self.Status.TOMBSTONED
        self.external_active = False
        self.tombstoned_at = timezone.now()

    def mark_unlinked(self):
        self.status = self.Status.UNLINKED
        self.unlinked_at = timezone.now()


class QuickBooksSyncAttempt(models.Model):
    class EntityType(models.TextChoices):
        CUSTOMER = 'customer', 'Customer'

    class Operation(models.TextChoices):
        CREATE = 'create', 'Create'
        READ = 'read', 'Read'
        UPDATE = 'update', 'Update'

    class Direction(models.TextChoices):
        TO_QUICKBOOKS = 'to_quickbooks', 'To QuickBooks'
        FROM_QUICKBOOKS = 'from_quickbooks', 'From QuickBooks'

    class Status(models.TextChoices):
        RUNNING = 'running', 'Running'
        SUCCEEDED = 'succeeded', 'Succeeded'
        FAILED = 'failed', 'Failed'
        RESOLVED = 'resolved', 'Resolved'

    connection = models.ForeignKey(
        QuickBooksConnection,
        on_delete=models.PROTECT,
        related_name='sync_attempts',
    )
    project = models.ForeignKey(
        Project,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='quickbooks_sync_attempts',
    )
    mapping = models.ForeignKey(
        QuickBooksProjectCustomerMapping,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='sync_attempts',
    )
    entity_type = models.CharField(max_length=20, choices=EntityType.choices)
    operation = models.CharField(max_length=20, choices=Operation.choices)
    direction = models.CharField(max_length=20, choices=Direction.choices)
    status = models.CharField(max_length=20, choices=Status.choices)
    operation_key = models.UUIDField(default=uuid.uuid4, editable=False)
    attempt_number = models.PositiveSmallIntegerField(default=1)
    request_payload = models.JSONField(default=dict, blank=True)
    response_snapshot = models.JSONField(default=dict, blank=True)
    external_id = models.CharField(max_length=50, blank=True)
    external_sync_token = models.CharField(max_length=50, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.CharField(max_length=255, blank=True)
    retryable = models.BooleanField(default=False)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='quickbooks_sync_attempts_requested',
    )
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.CharField(max_length=255, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='quickbooks_sync_attempts_resolved',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)
        constraints = [
            models.UniqueConstraint(
                fields=('connection', 'operation_key', 'attempt_number'),
                name='integrations_unique_sync_attempt_number',
            ),
            models.UniqueConstraint(
                fields=('connection', 'entity_type', 'direction'),
                condition=models.Q(status='running'),
                name='integrations_unique_running_entity_sync',
            ),
        ]
        indexes = [
            models.Index(
                fields=('status', 'next_retry_at'),
                name='integrations_sync_retry_idx',
            ),
        ]

    def __str__(self):
        return (
            f'{self.get_entity_type_display()} {self.get_operation_display()} '
            f'#{self.attempt_number}: {self.get_status_display()}'
        )

    @property
    def request_id(self):
        return self.operation_key.hex
