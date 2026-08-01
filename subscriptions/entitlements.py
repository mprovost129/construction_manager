from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .models import OrganizationSubscription


def subscription_enforcement_enabled():
    return bool(getattr(settings, 'STRIPE_ENFORCE_SUBSCRIPTIONS', False))


def entitled_organization_ids():
    if not subscription_enforcement_enabled():
        return None
    past_due_cutoff = timezone.now() - timedelta(
        days=settings.STRIPE_PAST_DUE_GRACE_DAYS
    )
    return OrganizationSubscription.objects.filter(
        Q(status__in=OrganizationSubscription.ACCESS_STATUSES)
        | Q(
            status=OrganizationSubscription.Status.PAST_DUE,
            past_due_since__gte=past_due_cutoff,
        )
    ).values('organization_id')


def organization_has_access(organization):
    if not subscription_enforcement_enabled():
        return True
    try:
        return organization.subscription.access_allowed
    except OrganizationSubscription.DoesNotExist:
        return False
