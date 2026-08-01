from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from projects.access import projects_for_user
from projects.models import (
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
)
from subscriptions.models import (
    OrganizationSubscription,
    StripeWebhookEvent,
    SubscriptionCheckoutAttempt,
)
from subscriptions.services import (
    INTEGRATION_MARKER,
    create_or_reuse_subscription_checkout,
    process_stripe_webhook,
)

STRIPE_TEST_SETTINGS = {
    'APP_ENVIRONMENT': 'development',
    'STRIPE_SECRET_KEY': 'sk_test_example',
    'STRIPE_WEBHOOK_SECRET': 'whsec_example',
    'STRIPE_MONTHLY_SUBSCRIPTION_PRICE_ID': 'price_test_monthly',
    'STRIPE_YEARLY_SUBSCRIPTION_PRICE_ID': 'price_test_yearly',
    'STRIPE_SUBSCRIPTION_PRICE_IDS': {
        'standard_monthly': 'price_test_monthly',
        'standard_yearly': 'price_test_yearly',
    },
    'STRIPE_SUBSCRIPTIONS_CONFIGURED': True,
    'STRIPE_LIVE_MODE': False,
    'STRIPE_CHECKOUT_SESSION_MINUTES': 30,
    'STRIPE_PAST_DUE_GRACE_DAYS': 7,
    'STRIPE_AUTOMATIC_TAX_ENABLED': False,
    'STRIPE_ENFORCE_SUBSCRIPTIONS': False,
}


@override_settings(**STRIPE_TEST_SETTINGS)
class SubscriptionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(email='admin@example.com')
        self.staff = user_model.objects.create_user(email='staff@example.com')
        self.client_user = user_model.objects.create_user(email='client@example.com')
        self.outsider = user_model.objects.create_user(email='outsider@example.com')
        self.organization = Organization.objects.create(name='Acme', slug='acme')
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.admin,
            role=OrganizationMembership.Role.ADMIN,
        )
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.staff,
            role=OrganizationMembership.Role.STAFF,
        )
        self.project = Project.objects.create(
            organization=self.organization,
            name='Smith Residence',
            created_by=self.admin,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.client_user,
            role=OrganizationMembership.Role.CLIENT,
        )

    def checkout_response(self, attempt=None):
        expires_at = int((timezone.now() + timedelta(minutes=30)).timestamp())
        return SimpleNamespace(
            id='cs_test_123',
            url='https://checkout.stripe.com/c/pay/test-session',
            expires_at=expires_at,
        )

    def stripe_event(self, *, event_id, event_type, obj, created=None):
        return {
            'id': event_id,
            'type': event_type,
            'created': created or int(timezone.now().timestamp()),
            'livemode': False,
            'api_version': '2026-06-30.basil',
            'data': {'object': obj},
        }

    def create_checkout_attempt(self):
        request = self.factory.get('/')
        request.user = self.admin
        with patch(
            'subscriptions.services._create_remote_checkout_session',
            return_value=self.checkout_response(),
        ):
            return create_or_reuse_subscription_checkout(
                request=request,
                organization_id=self.organization.pk,
                actor=self.admin,
                plan_key='standard_monthly',
            )

    def complete_checkout(self, attempt):
        checkout = {
            'id': attempt.stripe_checkout_session_id,
            'mode': 'subscription',
            'livemode': False,
            'customer': 'cus_test_123',
            'subscription': 'sub_test_123',
            'metadata': {
                'integration': INTEGRATION_MARKER,
                'organization_id': str(self.organization.pk),
                'checkout_attempt_id': str(attempt.public_id),
                'plan_key': attempt.plan_key,
            },
        }
        event = self.stripe_event(
            event_id='evt_checkout_complete',
            event_type='checkout.session.completed',
            obj=checkout,
        )
        with patch('subscriptions.services.construct_stripe_event', return_value=event):
            return process_stripe_webhook(b'{}', 'signature')

    def subscription_event(
        self,
        *,
        event_id,
        status,
        created=None,
        plan_key='standard_monthly',
    ):
        price_id = {
            'standard_monthly': 'price_test_monthly',
            'standard_yearly': 'price_test_yearly',
        }.get(plan_key, 'price_test_unsupported')
        stripe_subscription = {
            'id': 'sub_test_123',
            'customer': 'cus_test_123',
            'status': status,
            'livemode': False,
            'cancel_at_period_end': False,
            'current_period_end': int(
                (timezone.now() + timedelta(days=30)).timestamp()
            ),
            'trial_end': None,
            'canceled_at': None,
            'metadata': {
                'integration': INTEGRATION_MARKER,
                'organization_id': str(self.organization.pk),
            },
            'items': {
                'data': [
                    {
                        'price': {
                            'id': price_id,
                            'lookup_key': plan_key,
                        }
                    }
                ]
            },
        }
        event = self.stripe_event(
            event_id=event_id,
            event_type='customer.subscription.updated',
            obj=stripe_subscription,
            created=created,
        )
        with (
            patch('subscriptions.services.construct_stripe_event', return_value=event),
            patch(
                'subscriptions.services._retrieve_remote_subscription',
                return_value=stripe_subscription,
            ),
        ):
            return process_stripe_webhook(b'{}', 'signature')

    def test_admin_can_open_subscription_page_and_staff_cannot(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('subscriptions:detail', args=(self.organization.slug,))
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SaaS access')
        self.assertContains(response, 'QuickBooks')
        self.assertContains(response, 'Standard Monthly')
        self.assertContains(response, 'Standard Yearly')

        self.client.force_login(self.staff)
        response = self.client.get(
            reverse('subscriptions:detail', args=(self.organization.slug,))
        )
        self.assertEqual(response.status_code, 403)

    def test_company_team_page_links_admin_to_subscription(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('projects:company_team', args=(self.organization.slug,))
        )
        self.assertContains(
            response,
            reverse('subscriptions:detail', args=(self.organization.slug,)),
        )

    def test_checkout_is_server_created_for_the_company_subscription(self):
        self.client.force_login(self.admin)
        with patch(
            'subscriptions.services._create_remote_checkout_session',
            return_value=self.checkout_response(),
        ) as remote_create:
            response = self.client.post(
                reverse(
                    'subscriptions:checkout_create',
                    args=(self.organization.slug,),
                ),
                {'plan_key': 'standard_monthly'},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self.checkout_response().url)
        attempt = SubscriptionCheckoutAttempt.objects.get()
        self.assertEqual(attempt.created_by, self.admin)
        self.assertEqual(attempt.plan_key, 'standard_monthly')
        self.assertEqual(attempt.stripe_price_id, 'price_test_monthly')
        self.assertEqual(attempt.status, SubscriptionCheckoutAttempt.Status.OPEN)
        params = remote_create.call_args.kwargs['params']
        self.assertEqual(params['mode'], 'subscription')
        self.assertEqual(params['line_items'][0]['price'], 'price_test_monthly')
        self.assertEqual(params['customer_email'], self.admin.email)
        self.assertEqual(
            params['subscription_data']['metadata']['organization_id'],
            str(self.organization.pk),
        )
        self.assertEqual(
            params['subscription_data']['metadata']['plan_key'],
            'standard_monthly',
        )
        self.assertIn('{CHECKOUT_SESSION_ID}', params['success_url'])
        self.assertTrue(remote_create.call_args.kwargs['idempotency_key'])

    def test_open_checkout_is_reused_to_prevent_duplicate_subscriptions(self):
        request = self.factory.get('/')
        request.user = self.admin
        with patch(
            'subscriptions.services._create_remote_checkout_session',
            return_value=self.checkout_response(),
        ) as remote_create:
            first = create_or_reuse_subscription_checkout(
                request=request,
                organization_id=self.organization.pk,
                actor=self.admin,
                plan_key='standard_monthly',
            )
            second = create_or_reuse_subscription_checkout(
                request=request,
                organization_id=self.organization.pk,
                actor=self.admin,
                plan_key='standard_monthly',
            )
        self.assertEqual(first.pk, second.pk)
        remote_create.assert_called_once()

    def test_yearly_checkout_uses_the_yearly_lookup_key_price(self):
        self.client.force_login(self.admin)
        with patch(
            'subscriptions.services._create_remote_checkout_session',
            return_value=self.checkout_response(),
        ) as remote_create:
            response = self.client.post(
                reverse(
                    'subscriptions:checkout_create',
                    args=(self.organization.slug,),
                ),
                {'plan_key': 'standard_yearly'},
            )

        self.assertEqual(response.status_code, 302)
        params = remote_create.call_args.kwargs['params']
        self.assertEqual(params['line_items'][0]['price'], 'price_test_yearly')
        attempt = SubscriptionCheckoutAttempt.objects.get()
        self.assertEqual(attempt.plan_key, 'standard_yearly')

    def test_unsupported_plan_does_not_contact_stripe(self):
        self.client.force_login(self.admin)
        with patch(
            'subscriptions.services._create_remote_checkout_session'
        ) as create:
            response = self.client.post(
                reverse(
                    'subscriptions:checkout_create',
                    args=(self.organization.slug,),
                ),
                {'plan_key': 'enterprise'},
            )

        self.assertRedirects(
            response,
            reverse('subscriptions:detail', args=(self.organization.slug,)),
        )
        create.assert_not_called()

    def test_checkout_return_does_not_grant_access_without_webhook(self):
        attempt = self.create_checkout_attempt()
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse('subscriptions:detail', args=(self.organization.slug,)),
            {'checkout': 'success', 'session_id': attempt.stripe_checkout_session_id},
        )
        subscription = OrganizationSubscription.objects.get(
            organization=self.organization
        )
        self.assertFalse(subscription.access_allowed)
        self.assertContains(response, 'signed subscription webhook')

    def test_signed_webhooks_map_checkout_and_activate_subscription_once(self):
        attempt = self.create_checkout_attempt()
        self.assertEqual(self.complete_checkout(attempt), 'processed')
        attempt.refresh_from_db()
        subscription = OrganizationSubscription.objects.get(
            organization=self.organization
        )
        self.assertEqual(attempt.status, SubscriptionCheckoutAttempt.Status.COMPLETE)
        self.assertEqual(subscription.stripe_customer_id, 'cus_test_123')
        self.assertEqual(subscription.stripe_subscription_id, 'sub_test_123')
        self.assertEqual(subscription.plan_key, 'standard_monthly')
        self.assertFalse(subscription.access_allowed)

        event_time = int(timezone.now().timestamp()) + 1
        self.assertEqual(
            self.subscription_event(
                event_id='evt_subscription_active',
                status='active',
                created=event_time,
            ),
            'processed',
        )
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, OrganizationSubscription.Status.ACTIVE)
        self.assertTrue(subscription.access_allowed)

        event = self.stripe_event(
            event_id='evt_subscription_active',
            event_type='customer.subscription.updated',
            obj={},
            created=event_time,
        )
        with patch('subscriptions.services.construct_stripe_event', return_value=event):
            self.assertEqual(process_stripe_webhook(b'{}', 'signature'), 'duplicate')
        self.assertEqual(
            StripeWebhookEvent.objects.filter(
                event_id='evt_subscription_active'
            ).count(),
            1,
        )

    def test_stale_subscription_event_does_not_overwrite_newer_state(self):
        attempt = self.create_checkout_attempt()
        self.complete_checkout(attempt)
        base_time = int(timezone.now().timestamp())
        self.subscription_event(
            event_id='evt_active_new',
            status='active',
            created=base_time + 20,
        )
        self.subscription_event(
            event_id='evt_past_due_old',
            status='past_due',
            created=base_time + 10,
        )
        subscription = OrganizationSubscription.objects.get(
            organization=self.organization
        )
        self.assertEqual(subscription.status, OrganizationSubscription.Status.ACTIVE)

    def test_customer_portal_plan_change_updates_to_supported_yearly_price(self):
        attempt = self.create_checkout_attempt()
        self.complete_checkout(attempt)
        self.subscription_event(
            event_id='evt_plan_changed',
            status='active',
            plan_key='standard_yearly',
        )
        subscription = OrganizationSubscription.objects.get(
            organization=self.organization
        )
        self.assertEqual(subscription.plan_key, 'standard_yearly')
        self.assertEqual(subscription.stripe_price_id, 'price_test_yearly')
        self.assertTrue(subscription.access_allowed)

    def test_unsupported_stripe_price_never_grants_access(self):
        attempt = self.create_checkout_attempt()
        self.complete_checkout(attempt)
        self.subscription_event(
            event_id='evt_unsupported_plan',
            status='active',
            plan_key='unsupported',
        )
        subscription = OrganizationSubscription.objects.get(
            organization=self.organization
        )
        self.assertEqual(subscription.plan_key, '')
        self.assertFalse(subscription.access_allowed)
        self.assertTrue(subscription.needs_attention)

    def test_past_due_grace_then_cancellation_controls_project_entitlement(self):
        attempt = self.create_checkout_attempt()
        self.complete_checkout(attempt)
        self.subscription_event(event_id='evt_past_due', status='past_due')
        subscription = OrganizationSubscription.objects.get(
            organization=self.organization
        )
        self.assertTrue(subscription.access_allowed)

        with override_settings(STRIPE_ENFORCE_SUBSCRIPTIONS=True):
            self.assertTrue(projects_for_user(self.admin).filter(pk=self.project.pk).exists())
            subscription.past_due_since = timezone.now() - timedelta(days=8)
            subscription.save(update_fields=('past_due_since', 'updated_at'))
            self.assertFalse(
                projects_for_user(self.admin).filter(pk=self.project.pk).exists()
            )

        self.subscription_event(event_id='evt_canceled', status='canceled')
        subscription.refresh_from_db()
        self.assertFalse(subscription.access_allowed)

    def test_customer_portal_is_available_for_existing_stripe_customer(self):
        subscription = OrganizationSubscription.objects.create(
            organization=self.organization,
            stripe_customer_id='cus_test_123',
            stripe_subscription_id='sub_test_123',
            plan_key='standard_monthly',
            stripe_price_id='price_test_monthly',
            status=OrganizationSubscription.Status.ACTIVE,
        )
        self.client.force_login(self.admin)
        with patch(
            'subscriptions.services._create_remote_portal_session',
            return_value=SimpleNamespace(url='https://billing.stripe.com/p/session/test'),
        ) as remote_portal:
            response = self.client.post(
                reverse(
                    'subscriptions:portal_create',
                    args=(self.organization.slug,),
                )
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://billing.stripe.com/p/session/test')
        self.assertEqual(
            remote_portal.call_args.kwargs['customer_id'],
            subscription.stripe_customer_id,
        )

    def test_invalid_webhook_signature_returns_400(self):
        self.client.logout()
        with patch(
            'subscriptions.services.stripe.Webhook.construct_event',
            side_effect=stripe_signature_error(),
        ):
            response = self.client.post(
                reverse('subscriptions:stripe_webhook'),
                data=b'{}',
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='bad',
            )
        self.assertEqual(response.status_code, 400)


def stripe_signature_error():
    import stripe

    return stripe.SignatureVerificationError('bad signature', 'bad')
