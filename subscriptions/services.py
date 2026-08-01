import logging
from datetime import UTC, datetime, timedelta

import stripe
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from projects.models import Organization

from .models import (
    OrganizationSubscription,
    StripeWebhookEvent,
    SubscriptionCheckoutAttempt,
)
from .plans import get_subscription_plan

logger = logging.getLogger(__name__)
INTEGRATION_MARKER = 'construction_manager_subscription'


class StripeSubscriptionUnavailable(Exception):
    pass


class StripeWebhookInvalid(Exception):
    pass


def stripe_subscriptions_configured():
    return bool(getattr(settings, 'STRIPE_SUBSCRIPTIONS_CONFIGURED', False))


def validate_stripe_configuration():
    if not stripe_subscriptions_configured():
        raise StripeSubscriptionUnavailable('Subscription billing is not configured.')
    if settings.APP_ENVIRONMENT == 'production' and not settings.STRIPE_LIVE_MODE:
        raise StripeSubscriptionUnavailable(
            'Production requires a Stripe live-mode secret key.'
        )
    if settings.APP_ENVIRONMENT != 'production' and settings.STRIPE_LIVE_MODE:
        raise StripeSubscriptionUnavailable(
            'Stripe live-mode keys cannot be used outside production.'
        )
    if not 30 <= settings.STRIPE_CHECKOUT_SESSION_MINUTES <= 1440:
        raise StripeSubscriptionUnavailable(
            'Stripe Checkout expiration must be between 30 minutes and 24 hours.'
        )


def _value(value, key, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _object_id(value):
    if isinstance(value, str):
        return value
    return _value(value, 'id', '') if value else ''


def _stripe_client():
    return stripe.StripeClient(settings.STRIPE_SECRET_KEY, max_network_retries=2)


def _create_remote_checkout_session(*, params, idempotency_key):
    return _stripe_client().v1.checkout.sessions.create(
        params,
        options={'idempotency_key': idempotency_key},
    )


def _create_remote_portal_session(*, customer_id, return_url):
    return _stripe_client().v1.billing_portal.sessions.create(
        {'customer': customer_id, 'return_url': return_url}
    )


def _retrieve_remote_subscription(stripe_subscription_id):
    return _stripe_client().v1.subscriptions.retrieve(stripe_subscription_id)


def _metadata(*, organization_id, checkout_attempt_id=None, plan_key=None):
    values = {
        'integration': INTEGRATION_MARKER,
        'organization_id': str(organization_id),
    }
    if checkout_attempt_id:
        values['checkout_attempt_id'] = str(checkout_attempt_id)
    if plan_key:
        values['plan_key'] = plan_key
    return values


@transaction.atomic
def create_or_reuse_subscription_checkout(
    *, request, organization_id, actor, plan_key
):
    validate_stripe_configuration()
    plan = get_subscription_plan(plan_key)
    if not plan:
        raise ValidationError('Choose a supported subscription plan.')
    organization = Organization.objects.select_for_update().get(pk=organization_id)
    subscription, _ = OrganizationSubscription.objects.select_for_update().get_or_create(
        organization=organization,
        defaults={'livemode': settings.STRIPE_LIVE_MODE},
    )
    if subscription.stripe_subscription_id and subscription.status not in (
        OrganizationSubscription.Status.CANCELED,
        OrganizationSubscription.Status.INCOMPLETE_EXPIRED,
    ):
        raise ValidationError(
            'This company already has a Stripe subscription. Use Manage billing.'
        )

    now = timezone.now()
    open_attempt = (
        organization.subscription_checkout_attempts.filter(
            status=SubscriptionCheckoutAttempt.Status.OPEN,
            livemode=settings.STRIPE_LIVE_MODE,
            expires_at__gt=now,
        )
        .exclude(checkout_url='')
        .first()
    )
    if open_attempt:
        if open_attempt.plan_key == plan.key:
            return open_attempt
        open_plan = get_subscription_plan(open_attempt.plan_key)
        open_label = open_plan.label if open_plan else 'another plan'
        raise ValidationError(
            f'A {open_label} checkout is already open. Complete it or wait for it '
            'to expire before choosing a different plan.'
        )
    organization.subscription_checkout_attempts.filter(
        status=SubscriptionCheckoutAttempt.Status.OPEN,
        expires_at__lte=now,
    ).update(status=SubscriptionCheckoutAttempt.Status.EXPIRED)

    stripe_price_id = settings.STRIPE_SUBSCRIPTION_PRICE_IDS.get(plan.key, '')
    if not stripe_price_id:
        raise StripeSubscriptionUnavailable(
            f'The Stripe Price for {plan.label} is not configured.'
        )
    expires_at = now + timedelta(minutes=settings.STRIPE_CHECKOUT_SESSION_MINUTES)
    attempt = SubscriptionCheckoutAttempt.objects.create(
        organization=organization,
        created_by=actor,
        plan_key=plan.key,
        stripe_price_id=stripe_price_id,
        livemode=settings.STRIPE_LIVE_MODE,
        expires_at=expires_at,
    )
    metadata = _metadata(
        organization_id=organization.pk,
        checkout_attempt_id=attempt.public_id,
        plan_key=plan.key,
    )
    detail_url = request.build_absolute_uri(
        reverse('subscriptions:detail', args=(organization.slug,))
    )
    params = {
        'mode': 'subscription',
        'line_items': [{'price': stripe_price_id, 'quantity': 1}],
        'client_reference_id': str(attempt.public_id),
        'metadata': metadata,
        'subscription_data': {'metadata': metadata},
        'success_url': (
            f'{detail_url}?checkout=success&session_id={{CHECKOUT_SESSION_ID}}'
        ),
        'cancel_url': f'{detail_url}?checkout=cancelled',
        'expires_at': int(expires_at.timestamp()),
    }
    if subscription.stripe_customer_id:
        params['customer'] = subscription.stripe_customer_id
    else:
        params['customer_email'] = actor.email
    if settings.STRIPE_AUTOMATIC_TAX_ENABLED:
        params['automatic_tax'] = {'enabled': True}
        params['tax_id_collection'] = {'enabled': True}

    try:
        checkout = _create_remote_checkout_session(
            params=params,
            idempotency_key=str(attempt.idempotency_key),
        )
    except stripe.StripeError as exc:
        logger.exception('Stripe subscription Checkout Session creation failed.')
        raise StripeSubscriptionUnavailable(
            'Stripe could not start subscription checkout. Please try again.'
        ) from exc
    session_id = _value(checkout, 'id', '')
    checkout_url = _value(checkout, 'url', '')
    if not session_id or not checkout_url:
        raise StripeSubscriptionUnavailable(
            'Stripe returned an incomplete Checkout Session.'
        )
    attempt.stripe_checkout_session_id = session_id
    attempt.checkout_url = checkout_url
    attempt.status = SubscriptionCheckoutAttempt.Status.OPEN
    remote_expires_at = _value(checkout, 'expires_at')
    if remote_expires_at:
        attempt.expires_at = datetime.fromtimestamp(int(remote_expires_at), tz=UTC)
    attempt.save(
        update_fields=(
            'stripe_checkout_session_id',
            'checkout_url',
            'status',
            'expires_at',
            'updated_at',
        )
    )
    return attempt


def create_customer_portal_session(*, request, subscription):
    validate_stripe_configuration()
    if not subscription.stripe_customer_id:
        raise ValidationError('Start a subscription before opening billing management.')
    return_url = request.build_absolute_uri(
        reverse('subscriptions:detail', args=(subscription.organization.slug,))
    )
    try:
        portal = _create_remote_portal_session(
            customer_id=subscription.stripe_customer_id,
            return_url=return_url,
        )
    except stripe.StripeError as exc:
        logger.exception('Stripe Customer Portal Session creation failed.')
        raise StripeSubscriptionUnavailable(
            'Stripe could not open billing management. Please try again.'
        ) from exc
    portal_url = _value(portal, 'url', '')
    if not portal_url:
        raise StripeSubscriptionUnavailable('Stripe returned an incomplete portal session.')
    return portal_url


def construct_stripe_event(payload, signature):
    try:
        validate_stripe_configuration()
        return stripe.Webhook.construct_event(
            payload,
            signature,
            settings.STRIPE_WEBHOOK_SECRET,
        )
    except (StripeSubscriptionUnavailable, ValueError, stripe.SignatureVerificationError) as exc:
        raise StripeWebhookInvalid('Invalid Stripe webhook request.') from exc


def _event_dict(event):
    if hasattr(event, 'to_dict_recursive'):
        return event.to_dict_recursive()
    return dict(event)


def _stripe_datetime(timestamp):
    if not timestamp:
        return None
    return datetime.fromtimestamp(int(timestamp), tz=UTC)


def _is_our_object(obj):
    return (_value(obj, 'metadata', {}) or {}).get('integration') == INTEGRATION_MARKER


def _subscription_reference(invoice):
    direct = _object_id(_value(invoice, 'subscription'))
    if direct:
        return direct
    parent = _value(invoice, 'parent', {}) or {}
    details = _value(parent, 'subscription_details', {}) or {}
    return _object_id(_value(details, 'subscription'))


def _subscription_period_end(stripe_subscription):
    direct = _value(stripe_subscription, 'current_period_end')
    if direct:
        return _stripe_datetime(direct)
    items = _value(_value(stripe_subscription, 'items', {}) or {}, 'data', []) or []
    ends = [_value(item, 'current_period_end') for item in items]
    ends = [value for value in ends if value]
    return _stripe_datetime(max(ends)) if ends else None


def _subscription_price_id(stripe_subscription):
    items = _value(_value(stripe_subscription, 'items', {}) or {}, 'data', []) or []
    if not items:
        return ''
    return _object_id(_value(items[0], 'price'))


def _subscription_plan_key(stripe_subscription, subscription):
    items = _value(_value(stripe_subscription, 'items', {}) or {}, 'data', []) or []
    if not items:
        return ''
    price = _value(items[0], 'price', {}) or {}
    price_id = _object_id(price)
    for plan_key, configured_price_id in settings.STRIPE_SUBSCRIPTION_PRICE_IDS.items():
        if price_id and price_id == configured_price_id:
            return plan_key
    return ''


def _handle_checkout_completed(checkout, event_created):
    if not _is_our_object(checkout):
        return False
    if _value(checkout, 'mode') != 'subscription':
        raise ValueError('Stripe Checkout mode is not subscription.')
    metadata = _value(checkout, 'metadata', {}) or {}
    attempt = (
        SubscriptionCheckoutAttempt.objects.select_for_update()
        .select_related('organization')
        .get(stripe_checkout_session_id=_value(checkout, 'id', ''))
    )
    if metadata.get('checkout_attempt_id') != str(attempt.public_id):
        raise ValueError('Stripe Checkout attempt metadata does not match.')
    if metadata.get('organization_id') != str(attempt.organization_id):
        raise ValueError('Stripe Checkout organization metadata does not match.')
    if metadata.get('plan_key') != attempt.plan_key:
        raise ValueError('Stripe Checkout plan metadata does not match.')
    if bool(_value(checkout, 'livemode', False)) != attempt.livemode:
        raise ValueError('Stripe Checkout mode does not match the local attempt.')
    customer_id = _object_id(_value(checkout, 'customer'))
    stripe_subscription_id = _object_id(_value(checkout, 'subscription'))
    if not customer_id or not stripe_subscription_id:
        raise ValueError('Completed subscription Checkout is missing Stripe identity.')
    subscription, _ = OrganizationSubscription.objects.select_for_update().get_or_create(
        organization=attempt.organization,
        defaults={'livemode': attempt.livemode},
    )
    if subscription.stripe_customer_id not in ('', customer_id):
        raise ValueError('Stripe Customer conflicts with the company billing identity.')
    replacing_ended_subscription = subscription.status in (
        OrganizationSubscription.Status.CANCELED,
        OrganizationSubscription.Status.INCOMPLETE_EXPIRED,
    )
    if (
        subscription.stripe_subscription_id not in ('', stripe_subscription_id)
        and not replacing_ended_subscription
    ):
        raise ValueError('Stripe Subscription conflicts with the company billing identity.')
    subscription.stripe_customer_id = customer_id
    subscription.stripe_subscription_id = stripe_subscription_id
    subscription.plan_key = attempt.plan_key
    subscription.stripe_price_id = attempt.stripe_price_id
    subscription.livemode = attempt.livemode
    subscription.last_synced_at = timezone.now()
    subscription.save(
        update_fields=(
            'stripe_customer_id',
            'stripe_subscription_id',
            'plan_key',
            'stripe_price_id',
            'livemode',
            'last_synced_at',
            'updated_at',
        )
    )
    attempt.status = SubscriptionCheckoutAttempt.Status.COMPLETE
    attempt.completed_at = event_created
    attempt.save(update_fields=('status', 'completed_at', 'updated_at'))
    return True


def _find_subscription(stripe_subscription):
    stripe_subscription_id = _value(stripe_subscription, 'id', '')
    metadata = _value(stripe_subscription, 'metadata', {}) or {}
    organization_id = metadata.get('organization_id')
    subscription = OrganizationSubscription.objects.select_for_update().filter(
        stripe_subscription_id=stripe_subscription_id
    ).first()
    if subscription:
        return subscription
    if metadata.get('integration') != INTEGRATION_MARKER or not organization_id:
        return None
    organization = Organization.objects.select_for_update().get(pk=organization_id)
    subscription, _ = OrganizationSubscription.objects.select_for_update().get_or_create(
        organization=organization,
        defaults={'livemode': bool(_value(stripe_subscription, 'livemode', False))},
    )
    return subscription


def _handle_subscription_changed(stripe_subscription, event_created):
    subscription = _find_subscription(stripe_subscription)
    if not subscription:
        return False
    if subscription.last_stripe_event_at and event_created < subscription.last_stripe_event_at:
        return True
    livemode = bool(_value(stripe_subscription, 'livemode', False))
    if livemode != settings.STRIPE_LIVE_MODE:
        raise ValueError('Stripe Subscription mode does not match this environment.')
    stripe_subscription_id = _value(stripe_subscription, 'id', '')
    customer_id = _object_id(_value(stripe_subscription, 'customer'))
    replacing_ended_subscription = subscription.status in (
        OrganizationSubscription.Status.CANCELED,
        OrganizationSubscription.Status.INCOMPLETE_EXPIRED,
    )
    if (
        subscription.stripe_subscription_id not in ('', stripe_subscription_id)
        and not replacing_ended_subscription
    ):
        raise ValueError('Stripe Subscription conflicts with the company billing identity.')
    if subscription.stripe_customer_id not in ('', customer_id):
        raise ValueError('Stripe Customer conflicts with the company billing identity.')
    status = _value(stripe_subscription, 'status', '')
    if status not in OrganizationSubscription.Status.values:
        raise ValueError(f'Unsupported Stripe Subscription status: {status}.')
    previous_status = subscription.status
    subscription.stripe_customer_id = customer_id
    subscription.stripe_subscription_id = stripe_subscription_id
    subscription.plan_key = _subscription_plan_key(stripe_subscription, subscription)
    subscription.stripe_price_id = _subscription_price_id(stripe_subscription)
    subscription.status = status
    subscription.livemode = livemode
    subscription.cancel_at_period_end = bool(
        _value(stripe_subscription, 'cancel_at_period_end', False)
    )
    subscription.current_period_end = _subscription_period_end(stripe_subscription)
    subscription.trial_end = _stripe_datetime(_value(stripe_subscription, 'trial_end'))
    subscription.canceled_at = _stripe_datetime(
        _value(stripe_subscription, 'canceled_at')
    )
    if status == OrganizationSubscription.Status.PAST_DUE:
        if previous_status != OrganizationSubscription.Status.PAST_DUE:
            subscription.past_due_since = event_created
    else:
        subscription.past_due_since = None
    subscription.last_stripe_event_at = event_created
    subscription.last_synced_at = timezone.now()
    subscription.save()
    return True


def _handle_subscription_invoice(invoice):
    stripe_subscription_id = _subscription_reference(invoice)
    if not stripe_subscription_id:
        return False
    subscription = OrganizationSubscription.objects.select_for_update().filter(
        stripe_subscription_id=stripe_subscription_id
    ).first()
    if not subscription:
        return False
    subscription.last_invoice_id = _value(invoice, 'id', '')
    subscription.last_invoice_status = _value(invoice, 'status', '') or ''
    subscription.last_synced_at = timezone.now()
    subscription.save(
        update_fields=(
            'last_invoice_id',
            'last_invoice_status',
            'last_synced_at',
            'updated_at',
        )
    )
    return True


def _handle_checkout_expired(checkout):
    if not _is_our_object(checkout):
        return False
    SubscriptionCheckoutAttempt.objects.filter(
        stripe_checkout_session_id=_value(checkout, 'id', '')
    ).update(status=SubscriptionCheckoutAttempt.Status.EXPIRED)
    return True


def _dispatch_event(event_type, obj, event_created):
    if event_type == 'checkout.session.completed':
        return _handle_checkout_completed(obj, event_created)
    if event_type == 'checkout.session.expired':
        return _handle_checkout_expired(obj)
    if event_type in (
        'customer.subscription.created',
        'customer.subscription.updated',
        'customer.subscription.deleted',
        'customer.subscription.paused',
        'customer.subscription.resumed',
    ):
        stripe_subscription_id = _value(obj, 'id', '')
        if not stripe_subscription_id:
            raise ValueError('Stripe Subscription event is missing its object ID.')
        current_subscription = _retrieve_remote_subscription(stripe_subscription_id)
        return _handle_subscription_changed(current_subscription, event_created)
    if event_type in ('invoice.paid', 'invoice.payment_failed'):
        return _handle_subscription_invoice(obj)
    return False


def process_stripe_webhook(payload, signature):
    event = _event_dict(construct_stripe_event(payload, signature))
    event_id = event.get('id', '')
    event_type = event.get('type', '')
    livemode = bool(event.get('livemode', False))
    if not event_id or not event_type or not event.get('created'):
        raise ValueError('Stripe webhook event is missing required identity fields.')
    if livemode != settings.STRIPE_LIVE_MODE:
        raise ValueError('Stripe webhook mode does not match this environment.')
    obj = ((event.get('data') or {}).get('object') or {})
    event_created = _stripe_datetime(event['created'])
    try:
        with transaction.atomic():
            if StripeWebhookEvent.objects.filter(event_id=event_id).exists():
                return 'duplicate'
            event_record = StripeWebhookEvent.objects.create(
                event_id=event_id,
                event_type=event_type,
                object_id=_value(obj, 'id', '') or '',
                livemode=livemode,
                api_version=event.get('api_version') or '',
                outcome=StripeWebhookEvent.Outcome.IGNORED,
                created_at_stripe=event_created,
            )
            if _dispatch_event(event_type, obj, event_created):
                event_record.outcome = StripeWebhookEvent.Outcome.PROCESSED
                event_record.save(update_fields=('outcome',))
                return 'processed'
            return 'ignored'
    except IntegrityError:
        if StripeWebhookEvent.objects.filter(event_id=event_id).exists():
            return 'duplicate'
        raise
