from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from projects.models import ActivityEvent
from projects.services import record_activity

from .models import QuickBooksConnection
from .quickbooks import QuickBooksOAuthClient, QuickBooksOAuthError


class QuickBooksRealmConflict(Exception):
    pass


def _apply_token_response(connection, token_response, *, refreshed=False):
    now = timezone.now()
    connection.set_tokens(
        access_token=token_response.access_token,
        refresh_token=token_response.refresh_token,
    )
    connection.access_token_expires_at = now + timedelta(
        seconds=token_response.expires_in
    )
    if token_response.refresh_token_expires_in is not None:
        connection.refresh_token_expires_at = now + timedelta(
            seconds=token_response.refresh_token_expires_in
        )
    connection.scopes = list(token_response.scopes or settings.QUICKBOOKS_SCOPES)
    connection.status = QuickBooksConnection.Status.CONNECTED
    connection.disconnected_at = None
    connection.clear_error()
    if refreshed:
        connection.last_refreshed_at = now


@transaction.atomic
def save_authorized_connection(*, organization, realm_id, token_response, actor):
    existing = (
        QuickBooksConnection.objects.select_for_update()
        .filter(
            environment=settings.QUICKBOOKS_ENVIRONMENT,
            realm_id=realm_id,
        )
        .first()
    )
    if existing and existing.organization_id != organization.pk:
        raise QuickBooksRealmConflict(
            'That QuickBooks company is already connected to another organization.'
        )

    reconnected = existing is not None
    connection = existing or QuickBooksConnection(
        organization=organization,
        realm_id=realm_id,
        environment=settings.QUICKBOOKS_ENVIRONMENT,
    )
    connection.organization = organization
    connection.connected_by = actor
    connection.connected_at = timezone.now()
    _apply_token_response(connection, token_response)
    connection.save()

    event_type = (
        ActivityEvent.Type.QUICKBOOKS_RECONNECTED
        if reconnected
        else ActivityEvent.Type.QUICKBOOKS_CONNECTED
    )
    action = 'reconnected' if reconnected else 'connected'
    record_activity(
        organization=organization,
        actor=actor,
        event_type=event_type,
        summary=f'QuickBooks company {realm_id} was {action}.',
        metadata={
            'realm_id': realm_id,
            'environment': settings.QUICKBOOKS_ENVIRONMENT,
        },
    )
    return connection


def refresh_connection(connection_id, *, client=None):
    refresh_error = None
    with transaction.atomic():
        connection = QuickBooksConnection.objects.select_for_update().get(
            pk=connection_id
        )
        if connection.status == QuickBooksConnection.Status.DISCONNECTED:
            raise QuickBooksOAuthError(
                'connection_disconnected',
                'This QuickBooks connection must be authorized again.',
            )
        client = client or QuickBooksOAuthClient()
        try:
            token_response = client.refresh(connection.refresh_token)
        except QuickBooksOAuthError as exc:
            connection.status = QuickBooksConnection.Status.REAUTHORIZATION_REQUIRED
            connection.last_error_code = exc.code
            connection.last_error_message = exc.public_message
            connection.save(
                update_fields=(
                    'status',
                    'last_error_code',
                    'last_error_message',
                    'updated_at',
                )
            )
            refresh_error = exc
        else:
            _apply_token_response(connection, token_response, refreshed=True)
            connection.save()
    if refresh_error:
        raise refresh_error
    return connection


def disconnect_connection(connection_id, *, actor, client=None):
    revoke_error = None
    with transaction.atomic():
        connection = (
            QuickBooksConnection.objects.select_for_update()
            .select_related('organization')
            .get(pk=connection_id)
        )
        if connection.status == QuickBooksConnection.Status.DISCONNECTED:
            return connection
        client = client or QuickBooksOAuthClient()
        try:
            client.revoke(connection.refresh_token)
        except QuickBooksOAuthError as exc:
            connection.status = QuickBooksConnection.Status.ERROR
            connection.last_error_code = exc.code
            connection.last_error_message = exc.public_message
            connection.save(
                update_fields=(
                    'status',
                    'last_error_code',
                    'last_error_message',
                    'updated_at',
                )
            )
            revoke_error = exc
        else:
            connection.mark_disconnected()
            connection.save()
            record_activity(
                organization=connection.organization,
                actor=actor,
                event_type=ActivityEvent.Type.QUICKBOOKS_DISCONNECTED,
                summary=(
                    f'QuickBooks company {connection.realm_id} was disconnected.'
                ),
                metadata={
                    'realm_id': connection.realm_id,
                    'environment': connection.environment,
                },
            )
    if revoke_error:
        raise revoke_error
    return connection
