import base64
from io import BytesIO

import qrcode
from django import forms
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_http_methods

from .mfa import (
    enrollment_required,
    issue_recovery_codes,
    mark_verified,
    safe_next,
    verified,
    verify_code,
)
from .models import AuthenticatorDevice, MFAAuditEvent


class PasswordForm(forms.Form):
    password = forms.CharField(label='Current password', max_length=256,
                               widget=forms.PasswordInput(attrs={'autocomplete': 'current-password', 'class': 'form-control'}))


class CodeForm(forms.Form):
    code = forms.CharField(label='Authenticator or recovery code', max_length=64,
                          widget=forms.TextInput(attrs={'autocomplete': 'one-time-code', 'class': 'form-control', 'autofocus': True}))
    use_recovery = forms.BooleanField(required=False, label='I am using a recovery code')


class ConfirmForm(forms.Form):
    code = forms.RegexField(r'^[0-9]{6}$', label='Six-digit authenticator code',
                           widget=forms.TextInput(attrs={'inputmode': 'numeric', 'autocomplete': 'one-time-code', 'class': 'form-control', 'maxlength': '6'}))


class RecoveryForm(CodeForm, PasswordForm):
    pass


class DisableMFAForm(RecoveryForm):
    confirm = forms.BooleanField(label='Turn off two-step verification and invalidate my recovery codes.')


def password_correct(request, form):
    account = authenticate(request=request, username=request.user.email,
                           password=form.cleaned_data['password'])
    if account is None or account.pk != request.user.pk:
        form.add_error(None, 'We could not verify your password. Please try again later if you have made several attempts.')
        return False
    return True


def device_for(request, *, locked=False):
    query = AuthenticatorDevice.objects
    if locked:
        query = query.select_for_update()
    return query.filter(account=request.user).first()


@login_required
@require_http_methods(['GET'])
def security(request):
    device = device_for(request)
    return render(request, 'users/security.html', {
        'enabled': bool(device and device.confirmed),
        'required': enrollment_required(request.user),
        'remaining_codes': device.recovery_codes.count() if device and device.confirmed else 0,
    })


def setup_is_current(request, device):
    return bool(device and not device.confirmed
                and request.session.get('mfa_setup_device') == device.pk
                and 0 <= (timezone.now() - device.setup_started_at).total_seconds() < settings.MFA_SETUP_SECONDS)


@sensitive_variables()
def setup_context(request, device, password_form, code_form):
    context = {'password_form': password_form, 'code_form': code_form, 'next': safe_next(request)}
    if setup_is_current(request, device):
        output = BytesIO()
        qrcode.make(device.config_url).save(output, format='PNG')
        context.update({'qr_code': base64.b64encode(output.getvalue()).decode(),
                        'manual_key': base64.b32encode(device.bin_key).decode(),
                        'setup_active': True})
    return context


@sensitive_post_parameters('password', 'code', 'cf-turnstile-response')
@login_required
@require_http_methods(['GET', 'POST'])
@sensitive_variables()
def setup(request):
    device = device_for(request)
    if device and device.confirmed:
        return redirect('users:security' if verified(request, device) else 'users:mfa_verify')
    password_form, code_form = PasswordForm(), ConfirmForm()
    if request.method == 'POST' and request.POST.get('step') == 'start':
        password_form = PasswordForm(request.POST)
        if password_form.is_valid() and password_correct(request, password_form):
            with transaction.atomic():
                # Serialize initial enrollment across sessions and workers.
                get_user_model().objects.select_for_update().get(pk=request.user.pk)
                device = device_for(request, locked=True)
                if device and device.confirmed:
                    return redirect('users:mfa_verify')
                if device:
                    device.delete()
                device = AuthenticatorDevice.objects.create(
                    account=request.user, name='Authenticator app', confirmed=False,
                )
                request.session['mfa_setup_device'] = device.pk
                MFAAuditEvent.objects.create(user=request.user, event='setup_started')
    elif request.method == 'POST' and request.POST.get('step') == 'confirm':
        code_form = ConfirmForm(request.POST)
        with transaction.atomic():
            device = device_for(request, locked=True)
            if not setup_is_current(request, device):
                password_form = PasswordForm({})
                password_form.add_error(None, 'Setup expired or was restarted. Enter your password to begin again.')
            elif code_form.is_valid():
                if verify_code(device, code_form.cleaned_data['code']):
                    device.confirmed = True
                    device.save()
                    codes = issue_recovery_codes(device)
                    request.session.pop('mfa_setup_device', None)
                    mark_verified(request, device, 'enrolled')
                    return render(request, 'users/recovery_codes.html', {'codes': codes})
                code_form.add_error('code', 'That code could not be verified. Wait for a new code and try again.')
    return render(request, 'users/mfa_setup.html', setup_context(request, device, password_form, code_form))


@sensitive_post_parameters('code', 'cf-turnstile-response')
@login_required
@require_http_methods(['GET', 'POST'])
def verify(request):
    device = device_for(request)
    if not device or not device.confirmed:
        return redirect('users:mfa_setup')
    if verified(request, device):
        return redirect(safe_next(request))
    form = CodeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            device = device_for(request, locked=True)
            if device and device.confirmed and verify_code(
                device, form.cleaned_data['code'], recovery=form.cleaned_data['use_recovery'],
            ):
                mark_verified(request, device, 'recovery_verified' if form.cleaned_data['use_recovery'] else 'verified')
                return redirect(safe_next(request))
        form.add_error('code', 'That code could not be verified. Check the code and wait before trying again.')
    return render(request, 'users/mfa_verify.html', {'form': form, 'next': safe_next(request)})


@sensitive_post_parameters('password', 'code', 'cf-turnstile-response')
@login_required
@require_http_methods(['GET', 'POST'])
@sensitive_variables()
def recovery_codes(request):
    device = device_for(request)
    if not verified(request, device):
        return redirect('users:mfa_verify')
    form = RecoveryForm(request.POST or None)
    if request.method == 'POST' and form.is_valid() and password_correct(request, form):
        with transaction.atomic():
            device = device_for(request, locked=True)
            if verified(request, device) and verify_code(
                device, form.cleaned_data['code'], recovery=form.cleaned_data['use_recovery'],
            ):
                codes = issue_recovery_codes(device)
                MFAAuditEvent.objects.create(user=request.user, event='recovery_codes_replaced')
                return render(request, 'users/recovery_codes.html', {'codes': codes})
        form.add_error('code', 'That code could not be verified. Wait for a new code and try again.')
    return render(request, 'users/recovery_regenerate.html', {'form': form})


@sensitive_post_parameters()
@login_required
@require_http_methods(['GET', 'POST'])
@sensitive_variables()
def disable(request):
    if enrollment_required(request.user):
        raise PermissionDenied('Your company requires two-step verification.')
    device = device_for(request)
    if not device or not device.confirmed:
        return redirect('users:security')
    if not verified(request, device):
        return redirect('users:mfa_verify')
    form = DisableMFAForm(request.POST or None)
    if request.method == 'POST' and form.is_valid() and password_correct(request, form):
        with transaction.atomic():
            # Serialize with team requirement changes and enrollment.
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            if enrollment_required(request.user):
                raise PermissionDenied('Your company now requires two-step verification.')
            device = device_for(request, locked=True)
            if verified(request, device) and verify_code(device, form.cleaned_data['code'],
                                                       recovery=form.cleaned_data['use_recovery']):
                device.delete()
                MFAAuditEvent.objects.create(user=request.user, event='disabled_by_owner')
                for key in ('mfa_device', 'mfa_user', 'mfa_at', 'mfa_setup_device'):
                    request.session.pop(key, None)
                request.session.cycle_key()
                return redirect('users:security')
        form.add_error('code', 'That code could not be verified. Wait for a new code and try again.')
    return render(request, 'users/mfa_disable.html', {'form': form})
