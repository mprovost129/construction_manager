from django.urls import path

from . import views

app_name = 'subscriptions'

urlpatterns = [
    path(
        'subscriptions/stripe/webhook/',
        views.StripeWebhookView.as_view(),
        name='stripe_webhook',
    ),
    path(
        'companies/<slug:slug>/subscription/',
        views.SubscriptionDetailView.as_view(),
        name='detail',
    ),
    path(
        'companies/<slug:slug>/subscription/checkout/',
        views.SubscriptionCheckoutCreateView.as_view(),
        name='checkout_create',
    ),
    path(
        'companies/<slug:slug>/subscription/portal/',
        views.SubscriptionPortalCreateView.as_view(),
        name='portal_create',
    ),
]
