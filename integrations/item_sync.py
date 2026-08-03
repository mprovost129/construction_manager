from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from projects.models import ActivityEvent, CostCode
from projects.services import record_activity

from .customer_sync import (
    QuickBooksSyncBusy,
    QuickBooksSyncError,
    _require_accounting_write,
)
from .models import QuickBooksConnection, QuickBooksItemMapping, QuickBooksSyncAttempt
from .quickbooks import QuickBooksAccountingClient, QuickBooksAPIError
from .services import QuickBooksMappingError, save_cost_code_item_mapping


def _item_snapshot(item):
    return {
        key: item.get(key)
        for key in ('Id', 'SyncToken', 'Name', 'FullyQualifiedName', 'Active', 'Type')
        if key in item
    }


def _create_running_attempt(
    *,
    connection,
    cost_code,
    item_mapping,
    operation,
    direction,
    request_payload,
    actor,
    operation_key=None,
    attempt_number=1,
):
    values = {
        'connection': connection,
        'cost_code': cost_code,
        'item_mapping': item_mapping,
        'entity_type': QuickBooksSyncAttempt.EntityType.ITEM,
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
            'An item synchronization is already running for this QuickBooks company.'
        ) from exc


def start_cost_code_item_sync(*, cost_code_id, connection_id, actor, api_client=None):
    cost_code = CostCode.objects.select_related('organization').get(pk=cost_code_id)
    connection = QuickBooksConnection.objects.get(pk=connection_id)
    if cost_code.organization_id != connection.organization_id:
        raise QuickBooksSyncError(
            'The cost code and QuickBooks connection must belong to the same company.'
        )
    if connection.status != QuickBooksConnection.Status.CONNECTED:
        raise QuickBooksSyncError('Reconnect QuickBooks before synchronizing items.')

    item_mapping = QuickBooksItemMapping.objects.filter(
        cost_code=cost_code,
        status=QuickBooksItemMapping.Status.ACTIVE,
    ).first()
    if item_mapping:
        if item_mapping.connection_id != connection.pk:
            raise QuickBooksSyncError(
                'This cost code must be synchronized through its mapped QuickBooks company.'
            )
        operation = QuickBooksSyncAttempt.Operation.READ
        direction = QuickBooksSyncAttempt.Direction.FROM_QUICKBOOKS
        payload = {'item_id': item_mapping.quickbooks_item_id}
    else:
        operation = QuickBooksSyncAttempt.Operation.CREATE
        direction = QuickBooksSyncAttempt.Direction.TO_QUICKBOOKS
        payload = {'Name': (cost_code.name or cost_code.code)[:100], 'Type': 'Service'}

    attempt = _create_running_attempt(
        connection=connection,
        cost_code=cost_code,
        item_mapping=item_mapping,
        operation=operation,
        direction=direction,
        request_payload=payload,
        actor=actor,
    )
    return execute_item_sync_attempt(attempt.pk, api_client=api_client)


def execute_item_sync_attempt(attempt_id, *, api_client=None):
    attempt = QuickBooksSyncAttempt.objects.select_related(
        'connection', 'cost_code__organization', 'item_mapping'
    ).get(pk=attempt_id)
    if attempt.status != QuickBooksSyncAttempt.Status.RUNNING:
        raise QuickBooksSyncError('Only running synchronization attempts can execute.')
    api_client = api_client or QuickBooksAccountingClient()
    try:
        item = _perform_item_operation(attempt, api_client)
        mapping = save_cost_code_item_mapping(
            cost_code=attempt.cost_code,
            connection=attempt.connection,
            item=item,
            actor=attempt.requested_by,
        )
    except QuickBooksAPIError as exc:
        if (
            attempt.operation == QuickBooksSyncAttempt.Operation.READ
            and exc.is_not_found
            and attempt.item_mapping_id
        ):
            return _mark_attempt_tombstoned(attempt.pk)
        return _mark_attempt_failed(attempt.pk, exc)
    except QuickBooksMappingError as exc:
        return _mark_attempt_failed(attempt.pk, exc)
    return _mark_attempt_succeeded(attempt.pk, mapping, item)


def _perform_item_operation(attempt, api_client):
    if attempt.operation == QuickBooksSyncAttempt.Operation.READ:
        return api_client.get_item(
            attempt.connection,
            attempt.request_payload['item_id'],
        )
    if attempt.operation == QuickBooksSyncAttempt.Operation.CREATE:
        _require_accounting_write(attempt.connection)
        name = attempt.request_payload['Name']
        matches = api_client.find_items_by_name(attempt.connection, name)
        if matches:
            return matches[0]
        return api_client.create_item(
            attempt.connection,
            attempt.request_payload,
            request_id=attempt.request_id,
        )
    raise QuickBooksSyncError('Unsupported item synchronization operation.')


@transaction.atomic
def _mark_attempt_succeeded(attempt_id, mapping, item):
    attempt = QuickBooksSyncAttempt.objects.select_for_update().get(pk=attempt_id)
    now = timezone.now()
    attempt.item_mapping = mapping
    attempt.status = QuickBooksSyncAttempt.Status.SUCCEEDED
    attempt.response_snapshot = _item_snapshot(item)
    attempt.external_id = str(item.get('Id') or '')
    attempt.external_sync_token = str(item.get('SyncToken') or '')
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
        organization=attempt.cost_code.organization,
        actor=attempt.requested_by,
        event_type=ActivityEvent.Type.QUICKBOOKS_ITEM_SYNC_SUCCEEDED,
        summary=(
            f'QuickBooks item sync succeeded for {attempt.cost_code.code} '
            f'(attempt {attempt.attempt_number}).'
        ),
        metadata={
            'sync_attempt_id': attempt.pk,
            'operation': attempt.operation,
            'quickbooks_item_id': attempt.external_id,
        },
    )
    return attempt


@transaction.atomic
def _mark_attempt_tombstoned(attempt_id):
    # Keep the locked query on the attempt table. PostgreSQL cannot apply
    # FOR UPDATE to nullable relations introduced by select_related().
    attempt = QuickBooksSyncAttempt.objects.select_for_update().get(pk=attempt_id)
    now = timezone.now()
    mapping = QuickBooksItemMapping.objects.select_for_update().get(
        pk=attempt.item_mapping_id
    )
    mapping.mark_tombstoned()
    mapping.save()
    attempt.status = QuickBooksSyncAttempt.Status.SUCCEEDED
    attempt.response_snapshot = dict(mapping.last_synced_values)
    attempt.response_snapshot['mapping_status'] = 'tombstoned'
    attempt.external_id = mapping.quickbooks_item_id
    attempt.external_sync_token = mapping.quickbooks_sync_token
    attempt.completed_at = now
    attempt.retryable = False
    attempt.next_retry_at = None
    attempt.save()
    record_activity(
        organization=attempt.cost_code.organization,
        actor=attempt.requested_by,
        event_type=ActivityEvent.Type.QUICKBOOKS_ITEM_MAPPING_TOMBSTONED,
        summary=(
            f'QuickBooks item {mapping.quickbooks_item_name} is no longer '
            'available; the mapping was preserved.'
        ),
        metadata={
            'sync_attempt_id': attempt.pk,
            'quickbooks_item_id': mapping.quickbooks_item_id,
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
        organization=attempt.cost_code.organization,
        actor=attempt.requested_by,
        event_type=ActivityEvent.Type.QUICKBOOKS_ITEM_SYNC_FAILED,
        summary=(
            f'QuickBooks item sync failed for {attempt.cost_code.code} '
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
def prepare_item_sync_retry(attempt_id, *, actor):
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
        cost_code=previous.cost_code,
        item_mapping=previous.item_mapping,
        operation=previous.operation,
        direction=previous.direction,
        request_payload=previous.request_payload,
        actor=actor,
        operation_key=previous.operation_key,
        attempt_number=previous.attempt_number + 1,
    )


def retry_item_sync_attempt(attempt_id, *, actor=None, api_client=None):
    attempt = prepare_item_sync_retry(attempt_id, actor=actor)
    return execute_item_sync_attempt(attempt.pk, api_client=api_client)


@transaction.atomic
def resolve_item_sync_attempt(attempt_id, *, actor, note):
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
        organization=attempt.cost_code.organization,
        actor=actor,
        event_type=ActivityEvent.Type.QUICKBOOKS_SYNC_RESOLVED,
        summary=f'QuickBooks sync issue was resolved for {attempt.cost_code.code}.',
        metadata={'sync_attempt_id': attempt.pk, 'error_code': attempt.error_code},
    )
    return attempt
