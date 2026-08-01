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
from projects.models import Organization, OrganizationMembership, Project

from .customer_sync import (
    QuickBooksSyncError,
    resolve_customer_sync_attempt,
    retry_customer_sync_attempt,
    start_project_customer_sync,
)
from .forms import QuickBooksProjectCustomerMappingForm
from .models import (
    QuickBooksConnection,
    QuickBooksProjectCustomerMapping,
    QuickBooksSyncAttempt,
)
from .quickbooks import (
    QuickBooksAccountingClient,
    QuickBooksAPIError,
    QuickBooksOAuthClient,
    QuickBooksOAuthError,
)
from .services import (
    QuickBooksMappingError,
    QuickBooksRealmConflict,
    disconnect_connection,
    record_capability_unavailable,
    refresh_company_capabilities,
    refresh_project_customer_mapping,
    save_authorized_connection,
    save_project_customer_mapping,
    unlink_project_customer_mapping,
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
    projects = Project.objects.none()
    mapping_form = None
    failed_sync_attempts = QuickBooksSyncAttempt.objects.none()
    recent_sync_attempts = QuickBooksSyncAttempt.objects.none()
    if selected_organization:
        connections = selected_organization.quickbooks_connections.all()
        projects = selected_organization.projects.select_related(
            'quickbooks_customer_mapping__connection'
        )
        mapping_form = QuickBooksProjectCustomerMappingForm(
            organization=selected_organization
        )
        failed_sync_attempts = QuickBooksSyncAttempt.objects.filter(
            connection__organization=selected_organization,
            status=QuickBooksSyncAttempt.Status.FAILED,
        ).select_related('project', 'connection')
        recent_sync_attempts = QuickBooksSyncAttempt.objects.filter(
            connection__organization=selected_organization,
        ).select_related('project', 'connection')[:20]
    return render(
        request,
        'integrations/quickbooks_connect.html',
        {
            'organizations': organizations,
            'selected_organization': selected_organization,
            'connections': connections,
            'projects': projects,
            'mapping_form': mapping_form,
            'failed_sync_attempts': failed_sync_attempts,
            'recent_sync_attempts': recent_sync_attempts,
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


@login_required
@require_POST
def quickbooks_capabilities_refresh(request, connection_id):
    connection = get_object_or_404(
        QuickBooksConnection.objects.select_related('organization'),
        pk=connection_id,
    )
    if not can_manage_organization(request.user, connection.organization):
        raise PermissionDenied('Only company administrators can inspect QuickBooks.')
    try:
        connection = refresh_company_capabilities(
            connection.pk,
            actor=request.user,
        )
    except QuickBooksAPIError as exc:
        messages.error(request, exc.public_message)
    else:
        messages.success(
            request,
            f'Capabilities refreshed for {connection.display_name}.',
        )
    return _connect_redirect(connection.organization)


@login_required
@require_POST
def quickbooks_mapping_save(request):
    organization = get_object_or_404(
        Organization,
        slug=request.POST.get('organization', ''),
    )
    if not can_manage_organization(request.user, organization):
        raise PermissionDenied('Only company administrators can map QuickBooks customers.')
    form = QuickBooksProjectCustomerMappingForm(
        request.POST,
        organization=organization,
    )
    if not form.is_valid():
        error = next(iter(form.errors.values()))[0]
        messages.error(request, str(error))
        return _connect_redirect(organization)

    connection = form.cleaned_data['connection']
    customer_id = form.cleaned_data['quickbooks_customer_id']
    try:
        customer = QuickBooksAccountingClient().get_customer(
            connection,
            customer_id,
        )
        mapping = save_project_customer_mapping(
            project=form.cleaned_data['project'],
            connection=connection,
            customer=customer,
            actor=request.user,
        )
    except QuickBooksAPIError as exc:
        if exc.is_feature_unsupported:
            record_capability_unavailable(connection.pk, 'customer_read', exc)
        messages.error(request, exc.public_message)
        return _connect_redirect(organization)
    except QuickBooksMappingError as exc:
        messages.error(request, str(exc))
        return _connect_redirect(organization)

    messages.success(
        request,
        f'{mapping.project.name} is mapped to {mapping.quickbooks_display_name}.',
    )
    return _connect_redirect(organization)


def _mapping_for_admin(request, mapping_id):
    mapping = get_object_or_404(
        QuickBooksProjectCustomerMapping.objects.select_related(
            'project__organization', 'connection'
        ),
        pk=mapping_id,
    )
    if not can_manage_organization(request.user, mapping.project.organization):
        raise PermissionDenied('Only company administrators can manage this mapping.')
    return mapping


@login_required
@require_POST
def quickbooks_mapping_refresh(request, mapping_id):
    mapping = _mapping_for_admin(request, mapping_id)
    try:
        mapping = refresh_project_customer_mapping(
            mapping.pk,
            actor=request.user,
        )
    except (QuickBooksAPIError, QuickBooksMappingError) as exc:
        messages.error(request, getattr(exc, 'public_message', str(exc)))
    else:
        if mapping.status == QuickBooksProjectCustomerMapping.Status.TOMBSTONED:
            messages.warning(
                request,
                'The QuickBooks customer no longer exists. The mapping history was preserved.',
            )
        else:
            messages.success(request, 'QuickBooks customer details were refreshed.')
    return _connect_redirect(mapping.project.organization)


@login_required
@require_POST
def quickbooks_mapping_unlink(request, mapping_id):
    mapping = _mapping_for_admin(request, mapping_id)
    mapping = unlink_project_customer_mapping(mapping.pk, actor=request.user)
    messages.success(request, 'The project was unlinked; mapping history was preserved.')
    return _connect_redirect(mapping.project.organization)


@login_required
@require_POST
def quickbooks_customer_sync(request, project_id):
    project = get_object_or_404(Project.objects.select_related('organization'), pk=project_id)
    if not can_manage_organization(request.user, project.organization):
        raise PermissionDenied('Only company administrators can synchronize customers.')
    connection = get_object_or_404(
        QuickBooksConnection,
        pk=request.POST.get('connection'),
        organization=project.organization,
    )
    try:
        attempt = start_project_customer_sync(
            project_id=project.pk,
            connection_id=connection.pk,
            actor=request.user,
        )
    except QuickBooksSyncError as exc:
        messages.error(request, str(exc))
    else:
        if attempt.status == QuickBooksSyncAttempt.Status.SUCCEEDED:
            messages.success(request, 'QuickBooks customer synchronization succeeded.')
        else:
            messages.error(request, attempt.error_message)
    return _connect_redirect(project.organization)


def _sync_attempt_for_admin(request, attempt_id):
    attempt = get_object_or_404(
        QuickBooksSyncAttempt.objects.select_related(
            'connection__organization', 'project'
        ),
        pk=attempt_id,
    )
    if not can_manage_organization(request.user, attempt.connection.organization):
        raise PermissionDenied('Only company administrators can manage sync errors.')
    return attempt


@login_required
@require_POST
def quickbooks_sync_retry(request, attempt_id):
    attempt = _sync_attempt_for_admin(request, attempt_id)
    try:
        result = retry_customer_sync_attempt(attempt.pk, actor=request.user)
    except QuickBooksSyncError as exc:
        messages.error(request, str(exc))
    else:
        if result.status == QuickBooksSyncAttempt.Status.SUCCEEDED:
            messages.success(request, 'QuickBooks customer synchronization succeeded.')
        else:
            messages.error(request, result.error_message)
    return _connect_redirect(attempt.connection.organization)


@login_required
@require_POST
def quickbooks_sync_resolve(request, attempt_id):
    attempt = _sync_attempt_for_admin(request, attempt_id)
    try:
        resolve_customer_sync_attempt(
            attempt.pk,
            actor=request.user,
            note=request.POST.get('resolution_note', ''),
        )
    except QuickBooksSyncError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'The synchronization issue was marked resolved.')
    return _connect_redirect(attempt.connection.organization)


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
