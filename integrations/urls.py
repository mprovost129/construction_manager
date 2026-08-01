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
        'quickbooks/disconnected/',
        views.quickbooks_disconnected,
        name='quickbooks_disconnected',
    ),
]
