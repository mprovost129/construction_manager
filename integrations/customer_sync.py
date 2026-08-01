from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from projects.models import ActivityEvent, Project
from projects.services import record_activity

from .models import (
    QuickBooksConnection,
    QuickBooksProjectCustomerMapping,
    QuickBooksSyncAttempt,
)
from .quickbooks import QuickBooksAccountingClient, QuickBooksAPIError
from .services import QuickBooksMappingError, save_project_customer_mapping


class QuickBooksSyncError(Exception):
    pass


class QuickBooksSyncBusy(QuickBooksSyncError):
    pass


def _customer_snapshot(customer):
    return {
        key: customer.get(key)
        for key in (
            'Id',
            'SyncToken',
            'DisplayName',
            'FullyQualifiedName',
            'Active',
            'IsProject',
            'ParentRef',
        )
        if key in customer
    }


def _create_running_attempt(
    *,
    connection,
    project,
    mapping,
    operation,
    direction,
    request_payload,
    actor,
    operation_key=None,
    attempt_number=1,
):
    values = {
        'connection': connection,
        'project': project,
        'mapping': mapping,
        'entity_type': QuickBooksSyncAttempt.EntityType.CUSTOMER,
        'operation': operation,
        'direction': direction,
        'status': QuickBooksSyncAttempt.Status.RUNNING,
        'attempt_number': attempt_number,
        'request_payload': request_payload,
        'requested_by': actor,
    }
    if operation_key:
        values['operation_key'] = operation_key
    try:
        return QuickBooksSyncAttempt.objects.create(**values)
    except IntegrityError as exc:
        raise QuickBooksSyncBusy(
            'A customer synchronization is already running for this QuickBooks company.'
        ) from exc


def start_project_customer_sync(
    *, project_id, connection_id, actor, api_client=None
):
    project = Project.objects.select_related('organization').get(pk=project_id)
    connection = QuickBooksConnection.objects.get(pk=connection_id)
    if project.organization_id != connection.organization_id:
        raise QuickBooksSyncError(
            'The project and QuickBooks connection must belong to the same company.'
        )
    if connection.status != QuickBooksConnection.Status.CONNECTED:
        raise QuickBooksSyncError('Reconnect QuickBooks before synchronizing customers.')

    mapping = QuickBooksProjectCustomerMapping.objects.filter(
        project=project,
        status=QuickBooksProjectCustomerMapping.Status.ACTIVE,
    ).first()
    if mapping:
        if mapping.connection_id != connection.pk:
            raise QuickBooksSyncError(
                'This project must be synchronized through its mapped QuickBooks company.'
            )
        operation = QuickBooksSyncAttempt.Operation.READ
        direction = QuickBooksSyncAttempt.Direction.FROM_QUICKBOOKS
        payload = {'customer_id': mapping.quickbooks_customer_id}
    else:
        operation = QuickBooksSyncAttempt.Operation.CREATE
        direction = QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS
        payload = {'DisplayName': project.name[:255]}

    attempt = _create_running_attempt(
        connection=connection,
        project=project,
        mapping=mapping,
        operation=operation,
        direction=direction,
        request_payload=payload,
        actor=actor,
    )
    return execute_customer_sync_attempt(attempt.pk, api_client=api_client)


def start_project_customer_update(*, project_id, actor, api_client=None):
    project = Project.objects.select_related('organization').get(pk=project_id)
    mapping = QuickBooksProjectCustomerMapping.objects.select_related('connection').filter(
        project=project,
        status=QuickBooksProjectCustomerMapping.Status.ACTIVE,
    ).first()
    if not mapping:
        raise QuickBooksSyncError(
            'Map this project to an active QuickBooks customer before updating it.'
        )
    connection = mapping.connection
    if connection.status != QuickBooksConnection.Status.CONNECTED:
        raise QuickBooksSyncError('Reconnect QuickBooks before updating this customer.')
    display_name = project.name.strip()
    if not display_name:
        raise QuickBooksSyncError('The project needs a name before updating QuickBooks.')

    attempt = _create_running_attempt(
        connection=connection,
        project=project,
        mapping=mapping,
        operation=QuickBooksSyncAttempt.Operation.UPDATE,
        direction=QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS,
        request_payload={
            'Id': mapping.quickbooks_customer_id,
            'DisplayName': display_name[:255],
        },
        actor=actor,
    )
    return execute_customer_sync_attempt(attempt.pk, api_client=api_client)


def execute_customer_sync_attempt(attempt_id, *, api_client=None):
    attempt = QuickBooksSyncAttempt.objects.select_related(
        'connection', 'project__organization', 'mapping'
    ).get(pk=attempt_id)
    if attempt.status != QuickBooksSyncAttempt.Status.RUNNING:
        raise QuickBooksSyncError('Only running synchronization attempts can execute.')
    api_client = api_client or QuickBooksAccountingClient()
    try:
        customer = _perform_customer_operation(attempt, api_client)
        mapping = save_project_customer_mapping(
            project=attempt.project,
            connection=attempt.connection,
            customer=customer,
            actor=attempt.requested_by,
        )
    except QuickBooksAPIError as exc:
        if (
            attempt.operation == QuickBooksSyncAttempt.Operation.READ
            and exc.is_not_found
            and attempt.mapping_id
        ):
            return _mark_attempt_tombstoned(attempt.pk)
        return _mark_attempt_failed(attempt.pk, exc)
    except QuickBooksMappingError as exc:
        return _mark_attempt_failed(attempt.pk, exc)
    return _mark_attempt_succeeded(attempt.pk, mapping, customer)


def _perform_customer_operation(attempt, api_client):
    if attempt.operation == QuickBooksSyncAttempt.Operation.READ:
        return api_client.get_customer(
            attempt.connection,
            attempt.request_payload['customer_id'],
        )
    if attempt.operation == QuickBooksSyncAttempt.Operation.CREATE:
        _require_accounting_write(attempt.connection)
        display_name = attempt.request_payload['DisplayName']
        matches = api_client.find_customers_by_display_name(
            attempt.connection,
            display_name,
        )
        if matches:
            return matches[0]
        return api_client.create_customer(
            attempt.connection,
            attempt.request_payload,
            request_id=attempt.request_id,
        )
    if attempt.operation == QuickBooksSyncAttempt.Operation.UPDATE:
        _require_accounting_write(attempt.connection)
        latest = api_client.get_customer(
            attempt.connection,
            attempt.request_payload['Id'],
        )
        if latest.get('SyncToken') is None:
            raise QuickBooksAPIError(
                'invalid_api_response',
                'QuickBooks returned a customer without a sync token.',
            )
        payload = dict(attempt.request_payload)
        if all(
            latest.get(key) == value
            for key, value in payload.items()
            if key not in {'Id', 'SyncToken'}
        ):
            return latest
        payload['SyncToken'] = latest['SyncToken']
        return api_client.update_customer(
            attempt.connection,
            payload,
            request_id=attempt.request_id,
        )
    raise QuickBooksSyncError('Unsupported customer synchronization operation.')


def _require_accounting_write(connection):
    if connection.capabilities_checked_at is None:
        raise QuickBooksAPIError(
            'capabilities_unknown',
            'Refresh QuickBooks company information before changing a customer.',
        )
    if not connection.capabilities.get('accounting_write', True):
        raise QuickBooksAPIError(
            '6190',
            'This QuickBooks subscription is currently read-only.',
        )


@transaction.atomic
def _mark_attempt_succeeded(attempt_id, mapping, customer):
    attempt = QuickBooksSyncAttempt.objects.select_for_update().get(pk=attempt_id)
    now = timezone.now()
    attempt.mapping = mapping
    attempt.status = QuickBooksSyncAttempt.Status.SUCCEEDED
    attempt.response_snapshot = _customer_snapshot(customer)
    attempt.external_id = str(customer.get('Id') or '')
    attempt.external_sync_token = str(customer.get('SyncToken') or '')
    attempt.completed_at = now
    attempt.error_code = ''
    attempt.error_message = ''
    attempt.retryable = False
    attempt.next_retry_at = None
    attempt.save()
    QuickBooksSyncAttempt.objects.filter(
        connection=attempt.connection,
        operation_key=attempt.operation_key,
        status=QuickBooksSyncAttempt.Status.FAILED,
    ).exclude(pk=attempt.pk).update(
        status=QuickBooksSyncAttempt.Status.RESOLVED,
        resolution_note=f'Superseded by successful attempt #{attempt.attempt_number}.',
        resolved_at=now,
    )
    record_activity(
        organization=attempt.project.organization,
        project=attempt.project,
        actor=attempt.requested_by,
        event_type=ActivityEvent.Type.QUICKBOOKS_CUSTOMER_SYNC_SUCCEEDED,
        summary=(
            f'QuickBooks customer sync succeeded for {attempt.project.name} '
            f'(attempt {attempt.attempt_number}).'
        ),
        metadata={
            'sync_attempt_id': attempt.pk,
            'operation': attempt.operation,
            'quickbooks_customer_id': attempt.external_id,
        },
    )
    return attempt


@transaction.atomic
def _mark_attempt_tombstoned(attempt_id):
    # Keep the locked query on the attempt table. PostgreSQL cannot apply
    # FOR UPDATE to nullable relations introduced by select_related().
    attempt = QuickBooksSyncAttempt.objects.select_for_update().get(pk=attempt_id)
    now = timezone.now()
    mapping = QuickBooksProjectCustomerMapping.objects.select_for_update().get(
        pk=attempt.mapping_id
    )
    mapping.mark_tombstoned()
    mapping.save()
    attempt.status = QuickBooksSyncAttempt.Status.SUCCEEDED
    attempt.response_snapshot = dict(mapping.last_synced_values)
    attempt.response_snapshot['mapping_status'] = 'tombstoned'
    attempt.external_id = mapping.quickbooks_customer_id
    attempt.external_sync_token = mapping.quickbooks_sync_token
    attempt.completed_at = now
    attempt.retryable = False
    attempt.next_retry_at = None
    attempt.save()
    record_activity(
        organization=attempt.project.organization,
        project=attempt.project,
        actor=attempt.requested_by,
        event_type=ActivityEvent.Type.QUICKBOOKS_PROJECT_MAPPING_TOMBSTONED,
        summary=(
            f'QuickBooks customer {mapping.quickbooks_display_name} is no longer '
            'available; the mapping was preserved.'
        ),
        metadata={
            'sync_attempt_id': attempt.pk,
            'quickbooks_customer_id': mapping.quickbooks_customer_id,
        },
    )
    return attempt


@transaction.atomic
def _mark_attempt_failed(attempt_id, error):
    attempt = QuickBooksSyncAttempt.objects.select_for_update().get(pk=attempt_id)
    now = timezone.now()
    attempt.status = QuickBooksSyncAttempt.Status.FAILED
    attempt.error_code = getattr(error, 'code', 'mapping_error')
    attempt.error_message = getattr(error, 'public_message', str(error))[:255]
    attempt.retryable = bool(getattr(error, 'retryable', False))
    if attempt.retryable and attempt.attempt_number < settings.QUICKBOOKS_SYNC_MAX_ATTEMPTS:
        delay = settings.QUICKBOOKS_SYNC_RETRY_BASE_SECONDS * (
            2 ** (attempt.attempt_number - 1)
        )
        attempt.next_retry_at = now + timedelta(seconds=min(delay, 3600))
    else:
        attempt.next_retry_at = None
    attempt.completed_at = now
    attempt.save()
    record_activity(
        organization=attempt.project.organization,
        project=attempt.project,
        actor=attempt.requested_by,
        event_type=ActivityEvent.Type.QUICKBOOKS_CUSTOMER_SYNC_FAILED,
        summary=(
            f'QuickBooks customer sync failed for {attempt.project.name} '
            f'(attempt {attempt.attempt_number}).'
        ),
        metadata={
            'sync_attempt_id': attempt.pk,
            'operation': attempt.operation,
            'error_code': attempt.error_code,
            'retryable': attempt.retryable,
        },
    )
    return attempt


@transaction.atomic
def prepare_customer_sync_retry(attempt_id, *, actor):
    previous = QuickBooksSyncAttempt.objects.select_for_update().get(pk=attempt_id)
    latest = QuickBooksSyncAttempt.objects.filter(
        connection=previous.connection,
        operation_key=previous.operation_key,
    ).order_by('-attempt_number').first()
    if latest.pk != previous.pk or previous.status != QuickBooksSyncAttempt.Status.FAILED:
        raise QuickBooksSyncError('Only the latest failed attempt can be retried.')
    if not previous.retryable:
        raise QuickBooksSyncError('This failure requires review instead of automatic retry.')
    if previous.attempt_number >= settings.QUICKBOOKS_SYNC_MAX_ATTEMPTS:
        raise QuickBooksSyncError('This synchronization reached its retry limit.')

    previous.status = QuickBooksSyncAttempt.Status.RESOLVED
    previous.resolution_note = f'Retry attempt #{previous.attempt_number + 1} started.'
    previous.resolved_by = actor
    previous.resolved_at = timezone.now()
    previous.save()
    return _create_running_attempt(
        connection=previous.connection,
        project=previous.project,
        mapping=previous.mapping,
        operation=previous.operation,
        direction=previous.direction,
        request_payload=previous.request_payload,
        actor=actor,
        operation_key=previous.operation_key,
        attempt_number=previous.attempt_number + 1,
    )


def retry_customer_sync_attempt(attempt_id, *, actor=None, api_client=None):
    attempt = prepare_customer_sync_retry(attempt_id, actor=actor)
    return execute_customer_sync_attempt(attempt.pk, api_client=api_client)


@transaction.atomic
def resolve_customer_sync_attempt(attempt_id, *, actor, note):
    attempt = QuickBooksSyncAttempt.objects.select_for_update().get(pk=attempt_id)
    if attempt.status != QuickBooksSyncAttempt.Status.FAILED:
        raise QuickBooksSyncError('Only failed synchronization attempts can be resolved.')
    note = note.strip()
    if not note:
        raise QuickBooksSyncError('Describe how the synchronization issue was resolved.')
    attempt.status = QuickBooksSyncAttempt.Status.RESOLVED
    attempt.resolution_note = note[:255]
    attempt.resolved_by = actor
    attempt.resolved_at = timezone.now()
    attempt.next_retry_at = None
    attempt.save()
    record_activity(
        organization=attempt.project.organization,
        project=attempt.project,
        actor=actor,
        event_type=ActivityEvent.Type.QUICKBOOKS_SYNC_RESOLVED,
        summary=f'QuickBooks sync issue was resolved for {attempt.project.name}.',
        metadata={'sync_attempt_id': attempt.pk, 'error_code': attempt.error_code},
    )
    return attempt
