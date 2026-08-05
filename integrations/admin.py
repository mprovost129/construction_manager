from django.contrib import admin

from .models import (
    QuickBooksConnection,
    QuickBooksInvoiceMapping,
    QuickBooksItemMapping,
    QuickBooksPaymentMapping,
    QuickBooksProjectCustomerMapping,
    QuickBooksSyncAttempt,
)


@admin.register(QuickBooksConnection)
class QuickBooksConnectionAdmin(admin.ModelAdmin):
    list_display = (
        'organization',
        'realm_id',
        'environment',
        'status',
        'connected_at',
        'last_refreshed_at',
        'capabilities_checked_at',
    )
    list_filter = ('environment', 'status')
    search_fields = ('organization__name', 'realm_id')
    readonly_fields = (
        'organization',
        'realm_id',
        'environment',
        'status',
        'scopes',
        'company_name',
        'legal_name',
        'country',
        'subscription_status',
        'offering_sku',
        'capabilities',
        'capabilities_checked_at',
        'access_token_expires_at',
        'refresh_token_expires_at',
        'connected_by',
        'connected_at',
        'disconnected_at',
        'last_refreshed_at',
        'last_error_code',
        'last_error_message',
        'created_at',
        'updated_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(QuickBooksItemMapping)
class QuickBooksItemMappingAdmin(admin.ModelAdmin):
    list_display = (
        'cost_code',
        'quickbooks_item_name',
        'connection',
        'status',
        'last_seen_at',
    )
    list_filter = ('status', 'connection__environment')
    search_fields = (
        'cost_code__code',
        'quickbooks_item_name',
        'quickbooks_item_id',
    )
    readonly_fields = (
        'cost_code',
        'connection',
        'quickbooks_item_id',
        'quickbooks_sync_token',
        'quickbooks_item_name',
        'status',
        'external_active',
        'ownership_policy',
        'conflict_policy',
        'last_synced_values',
        'last_synced_at',
        'last_seen_at',
        'tombstoned_at',
        'unlinked_at',
        'created_at',
        'updated_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(QuickBooksProjectCustomerMapping)
class QuickBooksProjectCustomerMappingAdmin(admin.ModelAdmin):
    list_display = (
        'project',
        'quickbooks_display_name',
        'connection',
        'status',
        'last_seen_at',
    )
    list_filter = ('status', 'connection__environment')
    search_fields = (
        'project__name',
        'quickbooks_display_name',
        'quickbooks_customer_id',
    )
    readonly_fields = (
        'project',
        'connection',
        'quickbooks_customer_id',
        'quickbooks_sync_token',
        'quickbooks_display_name',
        'status',
        'external_active',
        'ownership_policy',
        'conflict_policy',
        'last_synced_values',
        'last_synced_at',
        'last_seen_at',
        'tombstoned_at',
        'unlinked_at',
        'created_at',
        'updated_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(QuickBooksInvoiceMapping)
class QuickBooksInvoiceMappingAdmin(admin.ModelAdmin):
    list_display = (
        'invoice',
        'quickbooks_doc_number',
        'connection',
        'status',
        'external_balance',
        'last_seen_at',
    )
    list_filter = ('status', 'connection__environment')
    search_fields = (
        'invoice__title',
        'quickbooks_doc_number',
        'quickbooks_invoice_id',
    )
    readonly_fields = tuple(
        field.name for field in QuickBooksInvoiceMapping._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(QuickBooksPaymentMapping)
class QuickBooksPaymentMappingAdmin(admin.ModelAdmin):
    list_display = (
        'payment',
        'quickbooks_payment_id',
        'connection',
        'status',
        'external_amount',
        'last_seen_at',
    )
    list_filter = ('status', 'connection__environment')
    search_fields = (
        'payment__invoice__title',
        'quickbooks_payment_id',
    )
    readonly_fields = tuple(
        field.name for field in QuickBooksPaymentMapping._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(QuickBooksSyncAttempt)
class QuickBooksSyncAttemptAdmin(admin.ModelAdmin):
    list_display = (
        'entity_type',
        'operation',
        'project',
        'connection',
        'attempt_number',
        'status',
        'created_at',
    )
    list_filter = ('entity_type', 'operation', 'direction', 'status', 'retryable')
    search_fields = ('project__name', 'connection__realm_id', 'error_code')
    readonly_fields = tuple(
        field.name for field in QuickBooksSyncAttempt._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
