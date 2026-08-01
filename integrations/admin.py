from django.contrib import admin

from .models import QuickBooksConnection, QuickBooksProjectCustomerMapping


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
