"""Separate Desktop identities: Online realm IDs and tokens are never reused."""
import uuid

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import models
from django.db.models import Q


class DesktopConnection(models.Model):
    organization = models.ForeignKey('projects.Organization', on_delete=models.PROTECT)
    username = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    password_hash = models.CharField(max_length=128, editable=False, default='!')
    active = models.BooleanField(default=False)
    expected_company_name = models.CharField(max_length=255)
    company_file_digest = models.CharField(max_length=64)
    file_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    verified_at = models.DateTimeField(null=True, editable=False)

    def set_password(self, password):
        self.password_hash = make_password(password)


class DesktopSession(models.Model):
    class State(models.TextChoices):
        AUTHENTICATED = 'authenticated'
        REQUESTED = 'requested'
        VERIFIED = 'verified'
        ERROR = 'error'
        CLOSED = 'closed'

    connection = models.OneToOneField(DesktopConnection, on_delete=models.CASCADE)
    ticket_digest = models.CharField(max_length=64, unique=True, editable=False)
    expires_at = models.DateTimeField()
    request_id = models.UUIDField(default=uuid.uuid4, editable=False)
    state = models.CharField(max_length=20, choices=State.choices, default=State.AUTHENTICATED)
    error_code = models.CharField(max_length=40, blank=True)
    response_digest = models.CharField(max_length=64, blank=True, editable=False)


class DesktopAuthThrottle(models.Model):
    """Shared across web workers; stores only hashed account/source identifiers."""

    key = models.CharField(max_length=64, primary_key=True)
    attempts = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()


class DesktopExport(models.Model):
    class State(models.TextChoices):
        PREPARED = 'prepared'
        UNCERTAIN = 'uncertain'
        CONFIRMED = 'confirmed'

    connection = models.ForeignKey(DesktopConnection, on_delete=models.PROTECT)
    invoice = models.OneToOneField('billing.Invoice', null=True, on_delete=models.PROTECT)
    credit_memo = models.OneToOneField('billing.CreditMemo', null=True, on_delete=models.PROTECT)
    request_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    content_digest = models.CharField(max_length=64, editable=False)
    company_file_digest = models.CharField(max_length=64, editable=False)
    state = models.CharField(max_length=20, choices=State.choices, default=State.PREPARED)
    txn_id = models.CharField(max_length=50, blank=True)
    edit_sequence = models.CharField(max_length=50, blank=True)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=(
                Q(invoice__isnull=False, credit_memo__isnull=True)
                | Q(invoice__isnull=True, credit_memo__isnull=False)
            ), name='desktop_export_exactly_one_source'),
            models.CheckConstraint(condition=(
                Q(state='confirmed') & ~Q(txn_id='') & ~Q(edit_sequence='')
                | Q(state__in=['prepared', 'uncertain'], txn_id='', edit_sequence='')
            ), name='desktop_export_receipt_matches_state'),
            models.UniqueConstraint(fields=['connection', 'txn_id'],
                                    condition=~Q(txn_id=''), name='desktop_unique_remote_transaction'),
        ]
