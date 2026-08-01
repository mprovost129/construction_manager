import secrets
import time
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from projects.access import can_manage_organization
from projects.models import Organization, OrganizationMembership

from .models import QuickBooksConnection
from .quickbooks import QuickBooksOAuthClient, QuickBooksOAuthError
from .services import (
    QuickBooksRealmConflict,
    disconnect_connection,
    save_authorized_connection,
)

OAUTH_SESSION_KEY = 'quickbooks_oauth_request'


def _manageable_organizations(user):
    if user.is_superuser:
        return Organization.objects.all()
    return Organization.objects.filter(
        memberships__user=user,
        memberships__is_active=True,
        memberships__role=OrganizationMembership.Role.ADMIN,
    ).distinct()


def _connect_redirect(organization=None):
    url = reverse('integrations:quickbooks_connect')
    if organization:
        url = f'{url}?{urlencode({"organization": organization.slug})}'
    return redirect(url)


@login_required
@require_GET
def quickbooks_connect(request):
    organizations = _manageable_organizations(request.user)
    organization_slug = request.GET.get('organization', '')
    selected_organization = None
    if organization_slug:
        selected_organization = get_object_or_404(
            organizations,
            slug=organization_slug,
        )
    elif organizations.count() == 1:
        selected_organization = organizations.first()

    connections = QuickBooksConnection.objects.none()
    if selected_organization:
        connections = selected_organization.quickbooks_connections.all()
    return render(
        request,
        'integrations/quickbooks_connect.html',
        {
            'organizations': organizations,
            'selected_organization': selected_organization,
            'connections': connections,
            'quickbooks_configured': settings.QUICKBOOKS_CONFIGURED,
            'quickbooks_environment': settings.QUICKBOOKS_ENVIRONMENT,
        },
    )


@login_required
@require_POST
def quickbooks_authorize(request):
    organization = get_object_or_404(
        Organization,
        slug=request.POST.get('organization', ''),
    )
    if not can_manage_organization(request.user, organization):
        raise PermissionDenied('Only company administrators can connect QuickBooks.')
    if not settings.QUICKBOOKS_CONFIGURED:
        messages.error(
            request,
            'QuickBooks credentials and token encryption must be configured first.',
        )
        return _connect_redirect(organization)

    state = secrets.token_urlsafe(32)
    request.session[OAUTH_SESSION_KEY] = {
        'state': state,
        'organization_id': organization.pk,
        'user_id': request.user.pk,
        'created_at': int(time.time()),
    }
    return redirect(QuickBooksOAuthClient().authorization_url(state))


@require_GET
def quickbooks_callback(request):
    oauth_request = request.session.pop(OAUTH_SESSION_KEY, None)
    if not request.user.is_authenticated:
        messages.error(request, 'Sign in and start the QuickBooks connection again.')
        return redirect(settings.LOGIN_URL)
    if not oauth_request:
        messages.error(request, 'The QuickBooks authorization request is missing or expired.')
        return _connect_redirect()

    received_state = request.GET.get('state', '')
    expected_state = oauth_request.get('state', '')
    state_age = int(time.time()) - oauth_request.get('created_at', 0)
    if (
        not received_state
        or not expected_state
        or not secrets.compare_digest(received_state, expected_state)
        or state_age < 0
        or state_age > settings.QUICKBOOKS_OAUTH_STATE_TTL_SECONDS
        or oauth_request.get('user_id') != request.user.pk
    ):
        messages.error(request, 'The QuickBooks authorization request was invalid or expired.')
        return _connect_redirect()

    organization = Organization.objects.filter(
        pk=oauth_request.get('organization_id')
    ).first()
    if not organization:
        messages.error(request, 'The company for this authorization no longer exists.')
        return _connect_redirect()
    if not can_manage_organization(request.user, organization):
        messages.error(
            request,
            'Your company permissions changed. Start the QuickBooks connection again.',
        )
        return _connect_redirect()

    if request.GET.get('error'):
        messages.error(
            request,
            'QuickBooks authorization was not completed. No connection was changed.',
        )
        return _connect_redirect(organization)

    code = request.GET.get('code', '')
    realm_id = request.GET.get('realmId', '')
    if not code or not realm_id or len(realm_id) > 50 or not realm_id.isdigit():
        messages.error(request, 'QuickBooks returned an incomplete authorization response.')
        return _connect_redirect(organization)
    if not settings.QUICKBOOKS_CONFIGURED:
        messages.error(request, 'QuickBooks configuration is no longer available.')
        return _connect_redirect(organization)

    try:
        token_response = QuickBooksOAuthClient().exchange_code(code)
        connection = save_authorized_connection(
            organization=organization,
            realm_id=realm_id,
            token_response=token_response,
            actor=request.user,
        )
    except (QuickBooksOAuthError, QuickBooksRealmConflict) as exc:
        public_message = getattr(exc, 'public_message', str(exc))
        messages.error(request, public_message)
        return _connect_redirect(organization)

    messages.success(
        request,
        f'QuickBooks company {connection.realm_id} is connected.',
    )
    return _connect_redirect(organization)


@login_required
@require_POST
def quickbooks_disconnect(request, connection_id):
    connection = get_object_or_404(
        QuickBooksConnection.objects.select_related('organization'),
        pk=connection_id,
    )
    if not can_manage_organization(request.user, connection.organization):
        raise PermissionDenied('Only company administrators can disconnect QuickBooks.')
    if not settings.QUICKBOOKS_CONFIGURED:
        messages.error(request, 'QuickBooks configuration is unavailable; access was not revoked.')
        return _connect_redirect(connection.organization)
    try:
        disconnect_connection(connection.pk, actor=request.user)
    except QuickBooksOAuthError as exc:
        messages.error(request, exc.public_message)
        return _connect_redirect(connection.organization)
    messages.success(request, 'QuickBooks access was revoked and disconnected.')
    return _connect_redirect(connection.organization)


@require_GET
def quickbooks_disconnected(request):
    realm_id = request.GET.get('realmId', '')
    if len(realm_id) > 50:
        return HttpResponseBadRequest('Invalid QuickBooks company identifier.')
    return render(
        request,
        'integrations/quickbooks_disconnected.html',
        {'realm_id': realm_id},
    )
