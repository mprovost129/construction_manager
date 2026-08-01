import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from projects.models import Organization

from .plans import SUBSCRIPTION_PLANS_BY_KEY, get_subscription_plan


class OrganizationSubscription(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = 'not_started', 'Not started'
        INCOMPLETE = 'incomplete', 'Incomplete'
        INCOMPLETE_EXPIRED = 'incomplete_expired', 'Incomplete expired'
        TRIALING = 'trialing', 'Trialing'
        ACTIVE = 'active', 'Active'
        PAST_DUE = 'past_due', 'Past due'
        CANCELED = 'canceled', 'Canceled'
        UNPAID = 'unpaid', 'Unpaid'
        PAUSED = 'paused', 'Paused'

    ACCESS_STATUSES = (Status.TRIALING, Status.ACTIVE)

    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        related_name='subscription',
    )
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True)
    plan_key = models.CharField(max_length=40, blank=True)
    stripe_price_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.NOT_STARTED,
    )
    livemode = models.BooleanField(default=False)
    cancel_at_period_end = models.BooleanField(default=False)
    current_period_end = models.DateTimeField(null=True, blank=True)
    trial_end = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    past_due_since = models.DateTimeField(null=True, blank=True)
    last_invoice_id = models.CharField(max_length=255, blank=True)
    last_invoice_status = models.CharField(max_length=40, blank=True)
    last_stripe_event_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('organization__name',)
        constraints = [
            models.UniqueConstraint(
                fields=('stripe_customer_id',),
                condition=~Q(stripe_customer_id=''),
                name='subscriptions_unique_stripe_customer',
            ),
            models.UniqueConstraint(
                fields=('stripe_subscription_id',),
                condition=~Q(stripe_subscription_id=''),
                name='subscriptions_unique_stripe_subscription',
            ),
        ]

    @property
    def access_allowed(self):
        if self.plan_key not in SUBSCRIPTION_PLANS_BY_KEY:
            return False
        if self.status in self.ACCESS_STATUSES:
            return True
        if self.status != self.Status.PAST_DUE or not self.past_due_since:
            return False
        grace_days = getattr(settings, 'STRIPE_PAST_DUE_GRACE_DAYS', 0)
        return self.past_due_since + timedelta(days=grace_days) >= timezone.now()

    @property
    def needs_attention(self):
        unsupported_plan = bool(self.stripe_subscription_id) and (
            self.plan_key not in SUBSCRIPTION_PLANS_BY_KEY
        )
        return unsupported_plan or self.status in (
            self.Status.INCOMPLETE,
            self.Status.INCOMPLETE_EXPIRED,
            self.Status.PAST_DUE,
            self.Status.UNPAID,
            self.Status.PAUSED,
            self.Status.CANCELED,
        )

    @property
    def plan_display(self):
        plan = get_subscription_plan(self.plan_key)
        return plan.label if plan else 'Unsupported plan'

    def __str__(self):
        return f'{self.organization}: {self.get_status_display()}'


class SubscriptionCheckoutAttempt(models.Model):
    class Status(models.TextChoices):
        CREATING = 'creating', 'Creating'
        OPEN = 'open', 'Open'
        COMPLETE = 'complete', 'Complete'
        EXPIRED = 'expired', 'Expired'

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='subscription_checkout_attempts',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='subscription_checkout_attempts',
    )
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True)
    plan_key = models.CharField(max_length=40)
    stripe_price_id = models.CharField(max_length=255)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    checkout_url = models.TextField(blank=True)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.CREATING,
    )
    livemode = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at', '-pk')
        constraints = [
            models.UniqueConstraint(
                fields=('stripe_checkout_session_id',),
                condition=~Q(stripe_checkout_session_id=''),
                name='subscriptions_unique_checkout_session',
            ),
        ]

    def __str__(self):
        return f'{self.organization}: subscription checkout {self.public_id}'


class StripeWebhookEvent(models.Model):
    class Outcome(models.TextChoices):
        PROCESSED = 'processed', 'Processed'
        IGNORED = 'ignored', 'Ignored'

    event_id = models.CharField(max_length=255, primary_key=True)
    event_type = models.CharField(max_length=100)
    object_id = models.CharField(max_length=255, blank=True)
    livemode = models.BooleanField()
    api_version = models.CharField(max_length=40, blank=True)
    outcome = models.CharField(max_length=12, choices=Outcome.choices)
    created_at_stripe = models.DateTimeField()
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-processed_at',)

    def __str__(self):
        return f'{self.event_type}: {self.event_id}'
