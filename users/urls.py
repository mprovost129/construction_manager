from django.urls import path

from . import mfa_views

app_name = 'users'
urlpatterns = [
    path('security/', mfa_views.security, name='security'),
    path('security/setup/', mfa_views.setup, name='mfa_setup'),
    path('security/verify/', mfa_views.verify, name='mfa_verify'),
    path('security/recovery-codes/', mfa_views.recovery_codes, name='mfa_recovery_codes'),
]
