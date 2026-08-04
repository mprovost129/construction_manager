import logging

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView

from projects.access import can_manage_organization
from projects.models import Organization

from .models import OrganizationSubscription, SubscriptionCheckoutAttempt
from .plans import SUBSCRIPTION_PLANS
from .services import (
    StripeSubscriptionUnavailable,
    StripeWebhookInvalid,
    create_customer_portal_session,
    create_or_reuse_subscription_checkout,
    process_stripe_webhook,
    stripe_subscriptions_configured,
)

logger = logging.getLogger(__name__)


def subscription_admin_or_403(user, slug):
    organization = get_object_or_404(Organization, slug=slug)
    if not can_manage_organization(user, organization):
        raise PermissionDenied('Only a company administrator can manage billing.')
    return organization


class SubscriptionDetailView(LoginRequiredMixin, TemplateView):
    template_name = 'subscriptions/detail.html'

    def dispatch(self, request, *args, **kwargs):
        self.organization = subscription_admin_or_403(request.user, kwargs['slug'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        subscription = OrganizationSubscription.objects.filter(
            organization=self.organization
        ).first()
        checkout_message = ''
        checkout_result = self.request.GET.get('checkout')
        if checkout_result == 'cancelled':
            checkout_message = 'Subscription checkout was canceled. No access changed.'
        elif checkout_result == 'success':
            session_id = self.request.GET.get('session_id', '')
            attempt = self.organization.subscription_checkout_attempts.filter(
                stripe_checkout_session_id=session_id
            ).first()
            if subscription and subscription.access_allowed:
                checkout_message = 'Subscription active. Your company has paid access.'
            elif attempt and attempt.status == SubscriptionCheckoutAttempt.Status.COMPLETE:
                checkout_message = (
                    'Checkout completed. Subscription activation is being confirmed.'
                )
            else:
                checkout_message = (
                    'Stripe received the checkout return. Access will change only after '
                    'the signed subscription webhook is processed.'
                )
        can_start = not subscription or subscription.status in (
            OrganizationSubscription.Status.NOT_STARTED,
            OrganizationSubscription.Status.CANCELED,
            OrganizationSubscription.Status.INCOMPLETE_EXPIRED,
        )
        context.update(
            {
                'organization': self.organization,
                'subscription': subscription,
                'stripe_configured': stripe_subscriptions_configured(),
                'subscription_enforced': settings.STRIPE_ENFORCE_SUBSCRIPTIONS,
                'can_start_checkout': can_start,
                'subscription_plans': SUBSCRIPTION_PLANS,
                'checkout_message': checkout_message,
            }
        )
        return context


class SubscriptionCheckoutCreateView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, slug):
        organization = subscription_admin_or_403(request.user, slug)
        try:
            attempt = create_or_reuse_subscription_checkout(
                request=request,
                organization_id=organization.pk,
                actor=request.user,
                plan_key=request.POST.get('plan_key', ''),
            )
        except (ValidationError, StripeSubscriptionUnavailable) as exc:
            message = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
            messages.error(request, message)
            return redirect('subscriptions:detail', slug=organization.slug)
        return redirect(attempt.checkout_url)


class SubscriptionPortalCreateView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, slug):
        organization = subscription_admin_or_403(request.user, slug)
        subscription = get_object_or_404(
            OrganizationSubscription,
            organization=organization,
        )
        try:
            portal_url = create_customer_portal_session(
                request=request,
                subscription=subscription,
            )
        except (ValidationError, StripeSubscriptionUnavailable) as exc:
            message = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
            messages.error(request, message)
            return redirect('subscriptions:detail', slug=organization.slug)
        return redirect(portal_url)


@method_decorator(csrf_exempt, name='dispatch')
class StripeWebhookView(View):
    http_method_names = ('post',)

    def post(self, request):
        signature = request.headers.get('Stripe-Signature', '')
        try:
            outcome = process_stripe_webhook(request.body, signature)
        except StripeWebhookInvalid:
            return HttpResponseBadRequest('Invalid Stripe signature.')
        except (ValueError, ValidationError, IntegrityError, stripe.StripeError):
            logger.exception('Stripe subscription webhook processing failed.')
            return HttpResponse(status=500)
        return JsonResponse({'received': True, 'outcome': outcome})
