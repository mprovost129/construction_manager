from functools import wraps

from django.conf import settings
from django.http import Http404
from django.urls import path

from . import desktop_views, views

app_name = 'integrations'


def online_path(route, view, *, name):
    @wraps(view)
    def enabled_view(request, *args, **kwargs):
        if not settings.QUICKBOOKS_INTEGRATION_ENABLED:
            raise Http404
        return view(request, *args, **kwargs)
    return path(route, enabled_view, name=name)

urlpatterns = [
    path('quickbooks/desktop/connector/', desktop_views.connector, name='desktop_connector'),
    path('quickbooks/setup/', views.quickbooks_setup, name='quickbooks_setup'),
    path(
        'quickbooks/connect/',
        views.quickbooks_connect,
        name='quickbooks_connect',
    ),
    online_path(
        'quickbooks/authorize/',
        views.quickbooks_authorize,
        name='quickbooks_authorize',
    ),
    online_path(
        'quickbooks/callback/',
        views.quickbooks_callback,
        name='quickbooks_callback',
    ),
    online_path(
        'quickbooks/connections/<int:connection_id>/disconnect/',
        views.quickbooks_disconnect,
        name='quickbooks_disconnect',
    ),
    online_path(
        'quickbooks/connections/<int:connection_id>/capabilities/refresh/',
        views.quickbooks_capabilities_refresh,
        name='quickbooks_capabilities_refresh',
    ),
    online_path(
        'quickbooks/mappings/save/',
        views.quickbooks_mapping_save,
        name='quickbooks_mapping_save',
    ),
    online_path(
        'quickbooks/mappings/<int:mapping_id>/refresh/',
        views.quickbooks_mapping_refresh,
        name='quickbooks_mapping_refresh',
    ),
    online_path(
        'quickbooks/mappings/<int:mapping_id>/unlink/',
        views.quickbooks_mapping_unlink,
        name='quickbooks_mapping_unlink',
    ),
    online_path(
        'quickbooks/customer-sync/projects/<int:project_id>/',
        views.quickbooks_customer_sync,
        name='quickbooks_customer_sync',
    ),
    online_path(
        'quickbooks/customer-sync/projects/<int:project_id>/update/',
        views.quickbooks_customer_update,
        name='quickbooks_customer_update',
    ),
    online_path(
        'quickbooks/item-mappings/save/',
        views.quickbooks_item_mapping_save,
        name='quickbooks_item_mapping_save',
    ),
    online_path(
        'quickbooks/item-mappings/<int:mapping_id>/refresh/',
        views.quickbooks_item_mapping_refresh,
        name='quickbooks_item_mapping_refresh',
    ),
    online_path(
        'quickbooks/item-mappings/<int:mapping_id>/unlink/',
        views.quickbooks_item_mapping_unlink,
        name='quickbooks_item_mapping_unlink',
    ),
    online_path(
        'quickbooks/item-sync/cost-codes/<int:cost_code_id>/',
        views.quickbooks_cost_code_item_sync,
        name='quickbooks_cost_code_item_sync',
    ),
    online_path(
        'quickbooks/invoice-sync/invoices/<int:invoice_id>/',
        views.quickbooks_invoice_sync,
        name='quickbooks_invoice_sync',
    ),
    online_path(
        'quickbooks/invoice-sync/invoices/<int:invoice_id>/void/',
        views.quickbooks_invoice_void_sync,
        name='quickbooks_invoice_void_sync',
    ),
    online_path(
        'quickbooks/invoice-mappings/<int:mapping_id>/refresh/',
        views.quickbooks_invoice_mapping_refresh,
        name='quickbooks_invoice_mapping_refresh',
    ),
    online_path(
        'quickbooks/payment-sync/invoices/<int:invoice_id>/',
        views.quickbooks_invoice_payment_sync,
        name='quickbooks_invoice_payment_sync',
    ),
    online_path(
        'quickbooks/sync-attempts/<int:attempt_id>/retry/',
        views.quickbooks_sync_retry,
        name='quickbooks_sync_retry',
    ),
    online_path(
        'quickbooks/sync-attempts/<int:attempt_id>/resolve/',
        views.quickbooks_sync_resolve,
        name='quickbooks_sync_resolve',
    ),
    online_path(
        'quickbooks/disconnected/',
        views.quickbooks_disconnected,
        name='quickbooks_disconnected',
    ),
]
