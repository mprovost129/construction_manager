from django.urls import path

from . import views

app_name = 'core'

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path(
        'legal/eula/',
        views.EndUserLicenseAgreementView.as_view(),
        name='eula',
    ),
    path(
        'legal/privacy/',
        views.PrivacyPolicyView.as_view(),
        name='privacy',
    ),
    path(
        'activity/',
        views.ProjectActivityListView.as_view(),
        name='activity_list',
    ),
    path(
        'activity/export.csv',
        views.ProjectActivityExportView.as_view(),
        name='activity_export',
    ),
]
