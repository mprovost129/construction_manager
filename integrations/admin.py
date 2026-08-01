from django.contrib import admin

from .models import QuickBooksConnection


@admin.register(QuickBooksConnection)
class QuickBooksConnectionAdmin(admin.ModelAdmin):
    list_display = (
        'organization',
        'realm_id',
        'environment',
        'status',
        'connected_at',
        'last_refreshed_at',
    )
    list_filter = ('environment', 'status')
    search_fields = ('organization__name', 'realm_id')
    readonly_fields = (
        'organization',
        'realm_id',
        'environment',
        'status',
        'scopes',
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
