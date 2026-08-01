from dataclasses import dataclass


@dataclass(frozen=True)
class SubscriptionPlan:
    key: str
    label: str
    price_display: str
    unit_amount: int
    interval: str
    interval_label: str
    description: str


SUBSCRIPTION_PLANS = (
    SubscriptionPlan(
        key='standard_monthly',
        label='Standard Monthly',
        price_display='$100/month',
        unit_amount=10_000,
        interval='month',
        interval_label='Monthly',
        description='Flexible month-to-month company access.',
    ),
    SubscriptionPlan(
        key='standard_yearly',
        label='Standard Yearly',
        price_display='$1,100/year',
        unit_amount=110_000,
        interval='year',
        interval_label='Yearly',
        description='One annual payment and $100 saved each year.',
    ),
)
SUBSCRIPTION_PLANS_BY_KEY = {plan.key: plan for plan in SUBSCRIPTION_PLANS}


def get_subscription_plan(plan_key):
    return SUBSCRIPTION_PLANS_BY_KEY.get(plan_key)
