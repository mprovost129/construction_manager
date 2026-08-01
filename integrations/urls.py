from django.urls import path

from . import views

app_name = 'integrations'

urlpatterns = [
    path(
        'quickbooks/connect/',
        views.quickbooks_connect,
        name='quickbooks_connect',
    ),
    path(
        'quickbooks/authorize/',
        views.quickbooks_authorize,
        name='quickbooks_authorize',
    ),
    path(
        'quickbooks/callback/',
        views.quickbooks_callback,
        name='quickbooks_callback',
    ),
    path(
        'quickbooks/connections/<int:connection_id>/disconnect/',
        views.quickbooks_disconnect,
        name='quickbooks_disconnect',
    ),
    path(
        'quickbooks/connections/<int:connection_id>/capabilities/refresh/',
        views.quickbooks_capabilities_refresh,
        name='quickbooks_capabilities_refresh',
    ),
    path(
        'quickbooks/mappings/save/',
        views.quickbooks_mapping_save,
        name='quickbooks_mapping_save',
    ),
    path(
        'quickbooks/mappings/<int:mapping_id>/refresh/',
        views.quickbooks_mapping_refresh,
        name='quickbooks_mapping_refresh',
    ),
    path(
        'quickbooks/mappings/<int:mapping_id>/unlink/',
        views.quickbooks_mapping_unlink,
        name='quickbooks_mapping_unlink',
    ),
    path(
        'quickbooks/customer-sync/projects/<int:project_id>/',
        views.quickbooks_customer_sync,
        name='quickbooks_customer_sync',
    ),
    path(
        'quickbooks/customer-sync/projects/<int:project_id>/update/',
        views.quickbooks_customer_update,
        name='quickbooks_customer_update',
    ),
    path(
        'quickbooks/sync-attempts/<int:attempt_id>/retry/',
        views.quickbooks_sync_retry,
        name='quickbooks_sync_retry',
    ),
    path(
        'quickbooks/sync-attempts/<int:attempt_id>/resolve/',
        views.quickbooks_sync_resolve,
        name='quickbooks_sync_resolve',
    ),
    path(
        'quickbooks/disconnected/',
        views.quickbooks_disconnected,
        name='quickbooks_disconnected',
    ),
]
