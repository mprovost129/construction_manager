# Stripe SaaS Subscription Setup

Stripe bills construction companies for access to Construction Manager. It does not collect
homeowner invoice payments. Each tenant's connected QuickBooks company remains responsible for
its homeowner invoices, payments, and accounting records.

## Integration shape

- One Stripe Customer and at most one current Stripe Subscription map to one local `Organization`.
- Company administrators start a subscription through Stripe-hosted Checkout.
- Company administrators manage payment methods, plan changes, and cancellation in Stripe's
  hosted Customer Portal.
- Signed Stripe webhooks are authoritative for entitlement state. A browser success redirect
  never grants access.
- Project access is filtered by the organization entitlement only when
  `STRIPE_ENFORCE_SUBSCRIPTIONS=true`.
- `active` and `trialing` subscriptions have access. `past_due` subscriptions retain access for
  `STRIPE_PAST_DUE_GRACE_DAYS`; canceled, unpaid, paused, incomplete, and expired subscriptions do
  not.

## Stripe Dashboard setup

Configure a Stripe Sandbox first, then repeat the same steps in live mode with separate IDs and
secrets.

The Sandbox currently has the two verified Prices, a default Customer Portal restricted to those
Prices, and the Render webhook endpoint registered for the event set below. Smart Retries, an
end-to-end Checkout/webhook exercise, and all live-mode configuration remain rollout actions.

1. Create a Construction Manager Product with exactly these active USD recurring Prices and copy
   their `price_...` IDs:

   - `standard_monthly`: $100 every month
   - `standard_yearly`: $1,100 every year

   The names above are the Stripe Price lookup keys. Each environment maps them to its own Price
   IDs through the environment variables below.
2. Configure the Customer Portal. Enable payment-method updates, invoice history, cancellation,
   and only the plan changes the application supports.
3. Configure Billing recovery, including Smart Retries and customer emails for failed payments.
4. Decide whether Stripe Tax applies. Enable `STRIPE_AUTOMATIC_TAX_ENABLED` only after business
   registrations and tax treatment have been reviewed.
5. Create a webhook endpoint at:

   `https://<application-host>/subscriptions/stripe/webhook/`

6. Subscribe the endpoint to:

   - `checkout.session.completed`
   - `checkout.session.expired`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `customer.subscription.paused`
   - `customer.subscription.resumed`
   - `invoice.paid`
   - `invoice.payment_failed`

7. Copy the endpoint's `whsec_...` signing secret. Do not reuse a Stripe CLI signing secret in
   production.

## Environment variables

Development/test mode:

```text
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_MONTHLY_SUBSCRIPTION_PRICE_ID=price_...
STRIPE_YEARLY_SUBSCRIPTION_PRICE_ID=price_...
STRIPE_CHECKOUT_SESSION_MINUTES=30
STRIPE_PAST_DUE_GRACE_DAYS=7
STRIPE_AUTOMATIC_TAX_ENABLED=false
STRIPE_ENFORCE_SUBSCRIPTIONS=false
```

Production uses `sk_live_...`, separate live monthly/yearly Price IDs, and the live endpoint signing
secret. Sandbox and live Stripe objects are intentionally not interchangeable.

## Safe rollout

1. Deploy the code and apply migrations with enforcement disabled.
2. Complete Checkout in Stripe test mode and verify the local company status becomes `active`
   only after the signed webhook is processed.
3. Verify Customer Portal changes, renewal payment, failed payment, past-due grace, cancellation,
   duplicate webhook delivery, and an expired Checkout Session.
4. Configure and validate both live Prices, portal, recovery settings, and webhook endpoint.
5. Establish subscriptions or an explicitly approved migration policy for every existing company.
6. Set `STRIPE_ENFORCE_SUBSCRIPTIONS=true` only after the company migration audit passes. This gate
   removes project access for organizations without a valid entitlement while leaving company
   billing administration reachable.

Stripe subscription revenue belongs in the software operator's accounting records. If desired,
sync it to the operator's own QuickBooks company separately; never write it to a tenant's connected
QuickBooks company.
