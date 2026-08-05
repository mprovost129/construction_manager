from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from projects.models import ActivityEvent
from projects.services import record_activity

from .models import (
    QuickBooksConnection,
    QuickBooksInvoiceMapping,
    QuickBooksItemMapping,
    QuickBooksPaymentMapping,
    QuickBooksProjectCustomerMapping,
)
from .quickbooks import (
    QuickBooksAccountingClient,
    QuickBooksAPIError,
    QuickBooksOAuthClient,
    QuickBooksOAuthError,
)


class QuickBooksRealmConflict(Exception):
    pass


class QuickBooksMappingError(Exception):
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
    connection.capabilities_checked_at = None
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


def _name_values(payload):
    values = {}
    for item in payload.get('NameValue') or []:
        if isinstance(item, dict) and item.get('Name'):
            values[str(item['Name']).casefold()] = item.get('Value', '')
    return values


def _nested(payload, *keys, default=None):
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def build_capability_profile(company_info, preferences):
    name_values = _name_values(company_info)
    subscription_status = str(
        company_info.get('SubscriptionStatus')
        or name_values.get('subscriptionstatus')
        or 'UNKNOWN'
    ).upper()
    write_enabled = subscription_status in {'TRIAL', 'TRIALOPTIN', 'SUBSCRIBED'}
    read_enabled = subscription_status != 'UNKNOWN'
    return {
        'accounting_read': read_enabled,
        'accounting_write': write_enabled,
        'customers': {'read': read_enabled, 'write': write_enabled},
        'invoices': {'read': read_enabled, 'write': write_enabled},
        'credit_memos': {'read': read_enabled, 'write': write_enabled},
        'payments': {'read': read_enabled, 'write': write_enabled},
        'custom_transaction_numbers': bool(
            _nested(preferences, 'SalesFormsPrefs', 'CustomTxnNumbers', default=False)
        ),
        'progress_invoicing': bool(
            _nested(
                preferences,
                'SalesFormsPrefs',
                'UsingProgressInvoicing',
                default=False,
            )
        ),
        'multicurrency': bool(
            _nested(
                preferences,
                'CurrencyPrefs',
                'MultiCurrencyEnabled',
                default=False,
            )
        ),
        'class_tracking': bool(
            _nested(preferences, 'AccountingInfoPrefs', 'ClassTrackingPerTxn')
            or _nested(preferences, 'AccountingInfoPrefs', 'ClassTrackingPerTxnLine')
        ),
        'location_tracking': bool(
            _nested(
                preferences,
                'AccountingInfoPrefs',
                'TrackDepartments',
                default=False,
            )
        ),
        'observed_unsupported': {},
    }


def _save_api_error(connection_id, error):
    connection = QuickBooksConnection.objects.get(pk=connection_id)
    connection.last_error_code = error.code
    connection.last_error_message = error.public_message
    if error.status_code == 401:
        connection.status = QuickBooksConnection.Status.REAUTHORIZATION_REQUIRED
    elif not error.retryable and not error.is_feature_unsupported:
        connection.status = QuickBooksConnection.Status.ERROR
    connection.save(
        update_fields=(
            'status',
            'last_error_code',
            'last_error_message',
            'updated_at',
        )
    )


def refresh_company_capabilities(connection_id, *, actor, api_client=None):
    connection = QuickBooksConnection.objects.select_related('organization').get(
        pk=connection_id
    )
    api_client = api_client or QuickBooksAccountingClient()
    try:
        company_info = api_client.get_company_info(connection)
        preferences = api_client.get_preferences(connection)
    except QuickBooksAPIError as exc:
        _save_api_error(connection.pk, exc)
        raise

    name_values = _name_values(company_info)
    now = timezone.now()
    with transaction.atomic():
        connection = QuickBooksConnection.objects.select_for_update().select_related(
            'organization'
        ).get(pk=connection_id)
        connection.company_name = str(company_info.get('CompanyName') or '')[:255]
        connection.legal_name = str(company_info.get('LegalName') or '')[:255]
        connection.country = str(company_info.get('Country') or '')[:10]
        connection.subscription_status = str(
            company_info.get('SubscriptionStatus')
            or name_values.get('subscriptionstatus')
            or 'UNKNOWN'
        ).upper()[:30]
        connection.offering_sku = str(name_values.get('offeringsku') or '')[:100]
        connection.capabilities = build_capability_profile(company_info, preferences)
        connection.capabilities_checked_at = now
        connection.status = QuickBooksConnection.Status.CONNECTED
        connection.clear_error()
        connection.save()
        record_activity(
            organization=connection.organization,
            actor=actor,
            event_type=ActivityEvent.Type.QUICKBOOKS_CAPABILITIES_REFRESHED,
            summary=f'QuickBooks capabilities refreshed for {connection.display_name}.',
            metadata={
                'realm_id': connection.realm_id,
                'subscription_status': connection.subscription_status,
                'offering_sku': connection.offering_sku,
            },
        )
    return connection


def record_capability_unavailable(connection_id, capability, error):
    with transaction.atomic():
        connection = QuickBooksConnection.objects.select_for_update().get(
            pk=connection_id
        )
        capabilities = dict(connection.capabilities)
        unsupported = dict(capabilities.get('observed_unsupported') or {})
        unsupported[capability] = {
            'code': error.code,
            'observed_at': timezone.now().isoformat(),
        }
        capabilities['observed_unsupported'] = unsupported
        capabilities[capability] = False
        connection.capabilities = capabilities
        connection.last_error_code = error.code
        connection.last_error_message = error.public_message
        connection.save(
            update_fields=(
                'capabilities',
                'last_error_code',
                'last_error_message',
                'updated_at',
            )
        )
    return connection


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


@transaction.atomic
def save_project_customer_mapping(*, project, connection, customer, actor):
    if project.organization_id != connection.organization_id:
        raise QuickBooksMappingError(
            'The project and QuickBooks connection must belong to the same company.'
        )
    if connection.status != QuickBooksConnection.Status.CONNECTED:
        raise QuickBooksMappingError(
            'Reconnect the QuickBooks company before saving a customer mapping.'
        )
    if customer.get('IsProject'):
        raise QuickBooksMappingError(
            'Choose a QuickBooks customer, not a QuickBooks Project or Job.'
        )
    customer_id = str(customer.get('Id') or '')
    display_name = str(
        customer.get('DisplayName') or customer.get('FullyQualifiedName') or ''
    )
    if not customer_id or not display_name:
        raise QuickBooksMappingError(
            'QuickBooks returned incomplete customer information.'
        )
    conflict = QuickBooksProjectCustomerMapping.objects.filter(
        connection=connection,
        quickbooks_customer_id=customer_id,
        status=QuickBooksProjectCustomerMapping.Status.ACTIVE,
    ).exclude(project=project)
    if conflict.exists():
        raise QuickBooksMappingError(
            'That QuickBooks customer is already mapped to another project.'
        )

    now = timezone.now()
    mapping, created = QuickBooksProjectCustomerMapping.objects.select_for_update().get_or_create(
        project=project,
        defaults={
            'connection': connection,
            'quickbooks_customer_id': customer_id,
            'quickbooks_display_name': display_name,
        },
    )
    identity_changed = (
        not created
        and (
            mapping.connection_id != connection.pk
            or mapping.quickbooks_customer_id != customer_id
            or mapping.status != QuickBooksProjectCustomerMapping.Status.ACTIVE
        )
    )
    mapping.connection = connection
    mapping.quickbooks_customer_id = customer_id
    mapping.quickbooks_sync_token = str(customer.get('SyncToken') or '')
    mapping.quickbooks_display_name = display_name[:255]
    mapping.status = QuickBooksProjectCustomerMapping.Status.ACTIVE
    mapping.external_active = bool(customer.get('Active', True))
    mapping.last_synced_values = _customer_snapshot(customer)
    mapping.last_synced_at = now
    mapping.last_seen_at = now
    mapping.tombstoned_at = None
    mapping.unlinked_at = None
    mapping.save()
    if created or identity_changed:
        record_activity(
            organization=project.organization,
            project=project,
            actor=actor,
            event_type=ActivityEvent.Type.QUICKBOOKS_PROJECT_MAPPED,
            summary=f'{project.name} was mapped to QuickBooks customer {display_name}.',
            metadata={
                'realm_id': connection.realm_id,
                'quickbooks_customer_id': customer_id,
            },
        )
    return mapping


def refresh_project_customer_mapping(mapping_id, *, actor, api_client=None):
    mapping = QuickBooksProjectCustomerMapping.objects.select_related(
        'connection', 'project__organization'
    ).get(pk=mapping_id)
    api_client = api_client or QuickBooksAccountingClient()
    try:
        customer = api_client.get_customer(
            mapping.connection,
            mapping.quickbooks_customer_id,
        )
    except QuickBooksAPIError as exc:
        if not exc.is_not_found:
            raise
        with transaction.atomic():
            mapping = QuickBooksProjectCustomerMapping.objects.select_for_update().get(
                pk=mapping_id
            )
            mapping.mark_tombstoned()
            mapping.save()
            record_activity(
                organization=mapping.project.organization,
                project=mapping.project,
                actor=actor,
                event_type=ActivityEvent.Type.QUICKBOOKS_PROJECT_MAPPING_TOMBSTONED,
                summary=(
                    f'QuickBooks customer {mapping.quickbooks_display_name} '
                    'is no longer available; the mapping was preserved.'
                ),
                metadata={
                    'quickbooks_customer_id': mapping.quickbooks_customer_id,
                },
            )
        return mapping
    return save_project_customer_mapping(
        project=mapping.project,
        connection=mapping.connection,
        customer=customer,
        actor=actor,
    )


@transaction.atomic
def unlink_project_customer_mapping(mapping_id, *, actor):
    mapping = QuickBooksProjectCustomerMapping.objects.select_for_update().select_related(
        'project__organization'
    ).get(pk=mapping_id)
    mapping.mark_unlinked()
    mapping.save()
    record_activity(
        organization=mapping.project.organization,
        project=mapping.project,
        actor=actor,
        event_type=ActivityEvent.Type.QUICKBOOKS_PROJECT_UNLINKED,
        summary=(
            f'{mapping.project.name} was unlinked from QuickBooks customer '
            f'{mapping.quickbooks_display_name}.'
        ),
        metadata={'quickbooks_customer_id': mapping.quickbooks_customer_id},
    )
    return mapping


def _item_snapshot(item):
    return {
        key: item.get(key)
        for key in ('Id', 'SyncToken', 'Name', 'FullyQualifiedName', 'Active', 'Type')
        if key in item
    }


@transaction.atomic
def save_cost_code_item_mapping(*, cost_code, connection, item, actor):
    if cost_code.organization_id != connection.organization_id:
        raise QuickBooksMappingError(
            'The cost code and QuickBooks connection must belong to the same company.'
        )
    if connection.status != QuickBooksConnection.Status.CONNECTED:
        raise QuickBooksMappingError(
            'Reconnect the QuickBooks company before saving an item mapping.'
        )
    item_id = str(item.get('Id') or '')
    item_name = str(item.get('Name') or item.get('FullyQualifiedName') or '')
    if not item_id or not item_name:
        raise QuickBooksMappingError(
            'QuickBooks returned incomplete item information.'
        )
    conflict = QuickBooksItemMapping.objects.filter(
        connection=connection,
        quickbooks_item_id=item_id,
        status=QuickBooksItemMapping.Status.ACTIVE,
    ).exclude(cost_code=cost_code)
    if conflict.exists():
        raise QuickBooksMappingError(
            'That QuickBooks item is already mapped to another cost code.'
        )

    now = timezone.now()
    mapping, created = QuickBooksItemMapping.objects.select_for_update().get_or_create(
        cost_code=cost_code,
        defaults={
            'connection': connection,
            'quickbooks_item_id': item_id,
            'quickbooks_item_name': item_name,
        },
    )
    identity_changed = (
        not created
        and (
            mapping.connection_id != connection.pk
            or mapping.quickbooks_item_id != item_id
            or mapping.status != QuickBooksItemMapping.Status.ACTIVE
        )
    )
    mapping.connection = connection
    mapping.quickbooks_item_id = item_id
    mapping.quickbooks_sync_token = str(item.get('SyncToken') or '')
    mapping.quickbooks_item_name = item_name[:255]
    mapping.status = QuickBooksItemMapping.Status.ACTIVE
    mapping.external_active = bool(item.get('Active', True))
    mapping.last_synced_values = _item_snapshot(item)
    mapping.last_synced_at = now
    mapping.last_seen_at = now
    mapping.tombstoned_at = None
    mapping.unlinked_at = None
    mapping.save()
    if created or identity_changed:
        record_activity(
            organization=cost_code.organization,
            actor=actor,
            event_type=ActivityEvent.Type.QUICKBOOKS_ITEM_MAPPED,
            summary=f'{cost_code.code} was mapped to QuickBooks item {item_name}.',
            metadata={
                'realm_id': connection.realm_id,
                'quickbooks_item_id': item_id,
            },
        )
    return mapping


def refresh_cost_code_item_mapping(mapping_id, *, actor, api_client=None):
    mapping = QuickBooksItemMapping.objects.select_related(
        'connection', 'cost_code__organization'
    ).get(pk=mapping_id)
    api_client = api_client or QuickBooksAccountingClient()
    try:
        item = api_client.get_item(
            mapping.connection,
            mapping.quickbooks_item_id,
        )
    except QuickBooksAPIError as exc:
        if not exc.is_not_found:
            raise
        with transaction.atomic():
            mapping = QuickBooksItemMapping.objects.select_for_update().get(
                pk=mapping_id
            )
            mapping.mark_tombstoned()
            mapping.save()
            record_activity(
                organization=mapping.cost_code.organization,
                actor=actor,
                event_type=ActivityEvent.Type.QUICKBOOKS_ITEM_MAPPING_TOMBSTONED,
                summary=(
                    f'QuickBooks item {mapping.quickbooks_item_name} '
                    'is no longer available; the mapping was preserved.'
                ),
                metadata={
                    'quickbooks_item_id': mapping.quickbooks_item_id,
                },
            )
        return mapping
    return save_cost_code_item_mapping(
        cost_code=mapping.cost_code,
        connection=mapping.connection,
        item=item,
        actor=actor,
    )


@transaction.atomic
def unlink_cost_code_item_mapping(mapping_id, *, actor):
    mapping = QuickBooksItemMapping.objects.select_for_update().select_related(
        'cost_code__organization'
    ).get(pk=mapping_id)
    mapping.mark_unlinked()
    mapping.save()
    record_activity(
        organization=mapping.cost_code.organization,
        actor=actor,
        event_type=ActivityEvent.Type.QUICKBOOKS_ITEM_UNLINKED,
        summary=(
            f'{mapping.cost_code.code} was unlinked from QuickBooks item '
            f'{mapping.quickbooks_item_name}.'
        ),
        metadata={'quickbooks_item_id': mapping.quickbooks_item_id},
    )
    return mapping


def _invoice_snapshot(invoice):
    return {
        key: invoice.get(key)
        for key in (
            'Id',
            'SyncToken',
            'DocNumber',
            'CustomerRef',
            'TotalAmt',
            'Balance',
            'TxnDate',
            'DueDate',
        )
        if key in invoice
    }


@transaction.atomic
def save_invoice_mapping(*, invoice, connection, quickbooks_invoice, actor):
    if invoice.organization_id != connection.organization_id:
        raise QuickBooksMappingError(
            'The invoice and QuickBooks connection must belong to the same company.'
        )
    if connection.status != QuickBooksConnection.Status.CONNECTED:
        raise QuickBooksMappingError(
            'Reconnect the QuickBooks company before saving an invoice mapping.'
        )
    quickbooks_invoice_id = str(quickbooks_invoice.get('Id') or '')
    if not quickbooks_invoice_id or quickbooks_invoice.get('SyncToken') is None:
        raise QuickBooksMappingError(
            'QuickBooks returned incomplete invoice information.'
        )
    customer_id = str((quickbooks_invoice.get('CustomerRef') or {}).get('value') or '')
    conflict = QuickBooksInvoiceMapping.objects.filter(
        connection=connection,
        quickbooks_invoice_id=quickbooks_invoice_id,
        status=QuickBooksInvoiceMapping.Status.ACTIVE,
    ).exclude(invoice=invoice)
    if conflict.exists():
        raise QuickBooksMappingError(
            'That QuickBooks invoice is already mapped to another invoice.'
        )

    now = timezone.now()
    mapping, created = QuickBooksInvoiceMapping.objects.select_for_update().get_or_create(
        invoice=invoice,
        defaults={
            'connection': connection,
            'quickbooks_invoice_id': quickbooks_invoice_id,
            'quickbooks_customer_id': customer_id,
        },
    )
    mapping.connection = connection
    mapping.quickbooks_sync_token = str(quickbooks_invoice.get('SyncToken') or '')
    mapping.quickbooks_customer_id = customer_id
    mapping.quickbooks_doc_number = str(quickbooks_invoice.get('DocNumber') or '')[:50]
    mapping.status = QuickBooksInvoiceMapping.Status.ACTIVE
    mapping.external_total_amount = quickbooks_invoice.get('TotalAmt')
    mapping.external_balance = quickbooks_invoice.get('Balance')
    mapping.external_txn_date = quickbooks_invoice.get('TxnDate')
    mapping.external_due_date = quickbooks_invoice.get('DueDate')
    mapping.currency_code = str(
        (quickbooks_invoice.get('CurrencyRef') or {}).get('value') or ''
    )[:10]
    mapping.last_synced_values = _invoice_snapshot(quickbooks_invoice)
    mapping.last_synced_at = now
    mapping.last_seen_at = now
    mapping.tombstoned_at = None
    mapping.full_clean()
    mapping.save()
    if created:
        record_activity(
            organization=invoice.organization,
            project=invoice.project,
            actor=actor,
            event_type=ActivityEvent.Type.QUICKBOOKS_INVOICE_MAPPED,
            summary=(
                f'{invoice.display_number} was synchronized to QuickBooks invoice '
                f'{mapping.quickbooks_doc_number or quickbooks_invoice_id}.'
            ),
            metadata={
                'realm_id': connection.realm_id,
                'quickbooks_invoice_id': quickbooks_invoice_id,
            },
        )
    return mapping


def refresh_invoice_mapping(mapping_id, *, actor, api_client=None):
    mapping = QuickBooksInvoiceMapping.objects.select_related(
        'connection', 'invoice__organization', 'invoice__project'
    ).get(pk=mapping_id)
    api_client = api_client or QuickBooksAccountingClient()
    try:
        invoice = api_client.get_invoice(
            mapping.connection,
            mapping.quickbooks_invoice_id,
        )
    except QuickBooksAPIError as exc:
        if not exc.is_not_found:
            raise
        with transaction.atomic():
            mapping = QuickBooksInvoiceMapping.objects.select_for_update().get(
                pk=mapping_id
            )
            mapping.mark_tombstoned()
            mapping.save()
            record_activity(
                organization=mapping.invoice.organization,
                project=mapping.invoice.project,
                actor=actor,
                event_type=ActivityEvent.Type.QUICKBOOKS_INVOICE_MAPPING_TOMBSTONED,
                summary=(
                    f'QuickBooks invoice '
                    f'{mapping.quickbooks_doc_number or mapping.quickbooks_invoice_id} '
                    'is no longer available; the mapping was preserved.'
                ),
                metadata={'quickbooks_invoice_id': mapping.quickbooks_invoice_id},
            )
        return mapping
    return save_invoice_mapping(
        invoice=mapping.invoice,
        connection=mapping.connection,
        quickbooks_invoice=invoice,
        actor=actor,
    )


def _payment_snapshot(payment):
    return {
        key: payment.get(key)
        for key in ('Id', 'SyncToken', 'TotalAmt', 'TxnDate', 'PaymentRefNum', 'PaymentMethodRef')
        if key in payment
    }


@transaction.atomic
def save_payment_mapping(*, invoice_mapping, connection, quickbooks_payment, local_payment, actor):
    if invoice_mapping.connection_id != connection.pk:
        raise QuickBooksMappingError(
            'The invoice mapping and QuickBooks connection must match.'
        )
    if local_payment.invoice_id != invoice_mapping.invoice_id:
        raise QuickBooksMappingError('The payment must belong to the mapped invoice.')
    quickbooks_payment_id = str(quickbooks_payment.get('Id') or '')
    if not quickbooks_payment_id or quickbooks_payment.get('SyncToken') is None:
        raise QuickBooksMappingError(
            'QuickBooks returned incomplete payment information.'
        )
    conflict = QuickBooksPaymentMapping.objects.filter(
        connection=connection,
        quickbooks_payment_id=quickbooks_payment_id,
        invoice_mapping=invoice_mapping,
        status=QuickBooksPaymentMapping.Status.ACTIVE,
    ).exclude(payment=local_payment)
    if conflict.exists():
        raise QuickBooksMappingError(
            'That QuickBooks payment is already mapped to another local payment.'
        )

    now = timezone.now()
    mapping, created = QuickBooksPaymentMapping.objects.select_for_update().get_or_create(
        payment=local_payment,
        defaults={
            'connection': connection,
            'invoice_mapping': invoice_mapping,
            'quickbooks_payment_id': quickbooks_payment_id,
        },
    )
    total_amt = quickbooks_payment.get('TotalAmt')
    mapping.connection = connection
    mapping.invoice_mapping = invoice_mapping
    mapping.quickbooks_payment_id = quickbooks_payment_id
    mapping.quickbooks_sync_token = str(quickbooks_payment.get('SyncToken') or '')
    mapping.status = (
        QuickBooksPaymentMapping.Status.VOIDED
        if total_amt is not None and Decimal(str(total_amt)) <= 0
        else QuickBooksPaymentMapping.Status.ACTIVE
    )
    mapping.external_amount = total_amt
    mapping.last_synced_values = _payment_snapshot(quickbooks_payment)
    mapping.last_synced_at = now
    mapping.last_seen_at = now
    mapping.tombstoned_at = None
    mapping.full_clean()
    mapping.save()
    if created:
        record_activity(
            organization=local_payment.invoice.organization,
            project=local_payment.invoice.project,
            actor=actor,
            event_type=ActivityEvent.Type.QUICKBOOKS_PAYMENT_IMPORTED,
            summary=(
                f'Imported a ${local_payment.amount:,.2f} payment from QuickBooks on '
                f'{local_payment.invoice.display_number}.'
            ),
            metadata={
                'realm_id': connection.realm_id,
                'quickbooks_payment_id': quickbooks_payment_id,
                'payment_id': local_payment.pk,
            },
        )
    return mapping


def refresh_payment_mapping(mapping_id, *, actor, api_client=None):
    mapping = QuickBooksPaymentMapping.objects.select_related(
        'connection',
        'invoice_mapping',
        'payment__invoice__organization',
        'payment__invoice__project',
    ).get(pk=mapping_id)
    api_client = api_client or QuickBooksAccountingClient()
    try:
        payment = api_client.get_payment(mapping.connection, mapping.quickbooks_payment_id)
    except QuickBooksAPIError as exc:
        if not exc.is_not_found:
            raise
        with transaction.atomic():
            mapping = QuickBooksPaymentMapping.objects.select_for_update().get(pk=mapping_id)
            mapping.mark_tombstoned()
            mapping.save()
            record_activity(
                organization=mapping.payment.invoice.organization,
                project=mapping.payment.invoice.project,
                actor=actor,
                event_type=ActivityEvent.Type.QUICKBOOKS_PAYMENT_MAPPING_TOMBSTONED,
                summary=(
                    f'QuickBooks payment {mapping.quickbooks_payment_id} is no longer '
                    'available; the mapping was preserved.'
                ),
                metadata={'quickbooks_payment_id': mapping.quickbooks_payment_id},
            )
        return mapping
    return save_payment_mapping(
        invoice_mapping=mapping.invoice_mapping,
        connection=mapping.connection,
        quickbooks_payment=payment,
        local_payment=mapping.payment,
        actor=actor,
    )


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
