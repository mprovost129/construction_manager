"""Browser form protection; authorization remains the responsibility of each view."""

import hashlib
from datetime import timedelta
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.shortcuts import render
from django.utils import timezone
from django.utils.cache import add_never_cache_headers

from .models import TurnstileRedemption

# These are machine protocols, not browser forms. They retain their own
# signature/credential validation and feature gates. Never exempt by URL prefix,
# content type, AJAX header, or csrf_exempt (which would exempt admin login too).
MACHINE_VIEWS = frozenset({
    'integrations:desktop_connector',
    'subscriptions:stripe_webhook',
})
TURNSTILE_ACTION = 'heritage_form'


def verify_form_token(request):
    token = request.POST.get('cf-turnstile-response', '')
    if (
        not settings.TURNSTILE_SITE_KEY or not settings.TURNSTILE_SECRET_KEY
        or not token or len(token) > 2048
    ):
        return False
    try:
        response = requests.post(
            'https://challenges.cloudflare.com/turnstile/v0/siteverify',
            data={'secret': settings.TURNSTILE_SECRET_KEY, 'response': token},
            timeout=5,
        )
        result = response.json() if response.ok else None
        hostname = urlsplit('https://' + request.get_host()).hostname
        if not (
            isinstance(result, dict) and result.get('success') is True
            and result.get('hostname') == hostname
            and result.get('action') == TURNSTILE_ACTION
        ):
            return False
        # Cloudflare replay detection is eventually consistent. Enforce single
        # redemption atomically across our own workers as well; store no tokens.
        with transaction.atomic():
            TurnstileRedemption.objects.filter(expires_at__lt=timezone.now()).delete()
            TurnstileRedemption.objects.create(
                digest=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=timezone.now() + timedelta(minutes=10),
            )
        return True
    except (requests.RequestException, ValueError, IntegrityError, DatabaseError):
        return False


class BrowserSecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # Protect HTML, private downloads, and invitation/reset pages from shared
        # caching. Static assets are served earlier by WhiteNoise in production.
        if 'no-store' not in response.get('Cache-Control', ''):
            add_never_cache_headers(response)
        response['Referrer-Policy'] = 'same-origin'
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not settings.TURNSTILE_FORMS_ENABLED or request.method in {'GET', 'HEAD', 'OPTIONS'}:
            return None
        if request.resolver_match.view_name in MACHINE_VIEWS:
            return None
        if not verify_form_token(request):
            return render(request, 'core/form_verification_failed.html', status=403)
        request.turnstile_verified = True
        return None


def form_security(request):
    return {
        'turnstile_forms_enabled': settings.TURNSTILE_FORMS_ENABLED,
        'form_turnstile_site_key': settings.TURNSTILE_SITE_KEY,
    }
