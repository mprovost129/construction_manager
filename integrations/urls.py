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
        'quickbooks/disconnected/',
        views.quickbooks_disconnected,
        name='quickbooks_disconnected',
    ),
]
