import hashlib
import re
import secrets
import time
from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.debug import sensitive_variables

from core.security import MACHINE_VIEWS

from .models import AuthenticatorDevice, MFAAuditEvent, MFARecoveryCode

FLOW_VIEWS = frozenset({'users:mfa_setup', 'users:mfa_verify', 'logout', 'admin:logout'})


def enrollment_required(user):
    """One active company's requirement applies to the account's sign-in."""
    from projects.models import OrganizationMembership
    return user.organization_memberships.filter(
        is_active=True, role__in=OrganizationMembership.INTERNAL_ROLES, mfa_required=True,
    ).exists()


def verified(request, device):
    return bool(device and device.confirmed
        and request.session.get('mfa_device') == device.pk
        and request.session.get('mfa_user') == request.user.pk
        and 0 <= time.time() - request.session.get('mfa_at', 0) < settings.MFA_SESSION_SECONDS)


def mark_verified(request, device, event):
    request.session.cycle_key()
    request.session['mfa_device'] = device.pk
    request.session['mfa_user'] = request.user.pk
    request.session['mfa_at'] = time.time()
    MFAAuditEvent.objects.create(user=request.user, event=event)


def safe_next(request):
    candidate = request.POST.get('next', request.GET.get('next', '/'))
    if (url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()},
                                        require_https=request.is_secure())
            and not candidate.startswith(reverse('users:security'))
            and not candidate.startswith('/accounts/login')
            and not candidate.startswith('/admin/login')):
        return candidate
    return '/'


def recovery_digest(code):
    return hashlib.sha256(code.replace('-', '').strip().lower().encode()).hexdigest()


@sensitive_variables()
def issue_recovery_codes(device):
    device.recovery_codes.all().delete()
    codes = ['-'.join([raw[i:i + 8] for i in range(0, 32, 8)])
             for raw in [secrets.token_hex(16) for _ in range(10)]]
    MFARecoveryCode.objects.bulk_create([
        MFARecoveryCode(device=device, digest=recovery_digest(code)) for code in codes
    ])
    return codes


def verify_code(device, code, *, recovery=False):
    """Caller locks the device inside a transaction for all token attempts."""
    if not device.verify_is_allowed()[0]:
        return False
    if recovery:
        deleted, _ = device.recovery_codes.filter(digest=recovery_digest(code)).delete()
        if deleted:
            device.throttle_reset()
            return True
        device.throttle_increment()
        return False
    if not re.fullmatch(r'[0-9]{6}', code):
        device.throttle_increment()
        return False
    return device.verify_token(code)


class AdministratorMFAMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not request.user.is_authenticated:
            return None
        name = request.resolver_match.view_name
        if name in MACHINE_VIEWS:
            return None
        device = AuthenticatorDevice.objects.filter(account=request.user).first()
        required = (device and device.confirmed) or (
            enrollment_required(request.user)
        )
        if not required or verified(request, device) or name in FLOW_VIEWS:
            return None
        target = reverse('users:mfa_verify' if device and device.confirmed else 'users:mfa_setup')
        target += '?' + urlencode({'next': request.get_full_path() if request.method == 'GET' else '/'})
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            return render(request, 'users/mfa_required.html', {'verification_url': target}, status=403)
        return redirect(target)
