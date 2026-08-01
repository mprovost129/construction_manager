from django.conf import settings
from django.core.checks import Error, Warning, register


@register()
def stripe_subscription_configuration_check(app_configs, **kwargs):
    issues = []
    configured_values = (
        settings.STRIPE_SECRET_KEY,
        settings.STRIPE_WEBHOOK_SECRET,
        settings.STRIPE_MONTHLY_SUBSCRIPTION_PRICE_ID,
        settings.STRIPE_YEARLY_SUBSCRIPTION_PRICE_ID,
    )
    has_any = any(configured_values)
    has_all = all(configured_values)
    if has_any and not has_all:
        issue_class = (
            Error
            if settings.APP_ENVIRONMENT == 'production'
            or settings.STRIPE_ENFORCE_SUBSCRIPTIONS
            else Warning
        )
        issues.append(
            issue_class(
                'Stripe subscription configuration is incomplete.',
                hint=(
                    'Set STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, '
                    'STRIPE_MONTHLY_SUBSCRIPTION_PRICE_ID, and '
                    'STRIPE_YEARLY_SUBSCRIPTION_PRICE_ID together.'
                ),
                id=(
                    'subscriptions.E001'
                    if issue_class is Error
                    else 'subscriptions.W002'
                ),
            )
        )
    if settings.STRIPE_ENFORCE_SUBSCRIPTIONS and not has_all:
        issues.append(
            Error(
                'Subscription enforcement requires complete Stripe configuration.',
                id='subscriptions.E002',
            )
        )
    if has_all and settings.APP_ENVIRONMENT == 'production' and not settings.STRIPE_LIVE_MODE:
        issues.append(
            Error(
                'Production Stripe subscriptions require a live-mode secret key.',
                id='subscriptions.E003',
            )
        )
    if has_all and settings.APP_ENVIRONMENT != 'production' and settings.STRIPE_LIVE_MODE:
        issues.append(
            Error(
                'A live Stripe key cannot be used outside production.',
                id='subscriptions.E004',
            )
        )
    if settings.STRIPE_ENFORCE_SUBSCRIPTIONS and not settings.STRIPE_SUBSCRIPTIONS_CONFIGURED:
        issues.append(
            Error(
                'Stripe subscription enforcement is enabled but billing is unavailable.',
                id='subscriptions.E005',
            )
        )
    if not 30 <= settings.STRIPE_CHECKOUT_SESSION_MINUTES <= 1440:
        issues.append(
            Error(
                'Stripe Checkout expiration must be between 30 and 1440 minutes.',
                id='subscriptions.E006',
            )
        )
    if settings.STRIPE_PAST_DUE_GRACE_DAYS < 0:
        issues.append(
            Error(
                'Stripe past-due grace days cannot be negative.',
                id='subscriptions.E007',
            )
        )
    price_ids = tuple(settings.STRIPE_SUBSCRIPTION_PRICE_IDS.values())
    invalid_price_ids = [value for value in price_ids if value and not value.startswith('price_')]
    if invalid_price_ids:
        issues.append(
            Error(
                'Stripe subscription Price IDs must start with price_.',
                id='subscriptions.E008',
            )
        )
    if all(price_ids) and len(set(price_ids)) != len(price_ids):
        issues.append(
            Error(
                'Monthly and yearly subscriptions must use different Stripe Prices.',
                id='subscriptions.E009',
            )
        )
    if not has_any:
        issues.append(
            Warning(
                'Stripe subscriptions are not configured; paid access is disabled.',
                id='subscriptions.W001',
            )
        )
    return issues
