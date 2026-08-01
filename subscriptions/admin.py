from django.contrib import admin

from .models import (
    OrganizationSubscription,
    StripeWebhookEvent,
    SubscriptionCheckoutAttempt,
)


class ReadOnlySubscriptionAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OrganizationSubscription)
class OrganizationSubscriptionAdmin(ReadOnlySubscriptionAdmin):
    list_display = (
        'organization',
        'status',
        'plan_key',
        'stripe_price_id',
        'cancel_at_period_end',
        'current_period_end',
        'livemode',
    )
    list_filter = ('status', 'plan_key', 'cancel_at_period_end', 'livemode')
    search_fields = (
        'organization__name',
        'stripe_customer_id',
        'stripe_subscription_id',
    )


@admin.register(SubscriptionCheckoutAttempt)
class SubscriptionCheckoutAttemptAdmin(ReadOnlySubscriptionAdmin):
    list_display = (
        'public_id',
        'organization',
        'plan_key',
        'status',
        'livemode',
        'created_at',
    )
    list_filter = ('plan_key', 'status', 'livemode')
    search_fields = ('organization__name', 'stripe_checkout_session_id')


@admin.register(StripeWebhookEvent)
class StripeWebhookEventAdmin(ReadOnlySubscriptionAdmin):
    list_display = ('event_id', 'event_type', 'outcome', 'livemode', 'processed_at')
    list_filter = ('outcome', 'livemode', 'event_type')
    search_fields = ('event_id', 'object_id')
