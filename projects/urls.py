from django.urls import path

from . import views

app_name = 'projects'

urlpatterns = [
    path('projects/<int:pk>/', views.ProjectDetailView.as_view(), name='detail'),
    path(
        'projects/<int:pk>/invite-client/',
        views.ClientInviteView.as_view(),
        name='invite_client',
    ),
    path(
        'invitations/<uuid:token>/',
        views.InvitationAcceptView.as_view(),
        name='accept_invitation',
    ),
]
