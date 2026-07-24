from django.urls import path

from . import views

app_name = 'projects'

urlpatterns = [
    path('projects/new/', views.ProjectCreateView.as_view(), name='create'),
    path('projects/<int:pk>/', views.ProjectDetailView.as_view(), name='detail'),
    path(
        'projects/<int:pk>/edit/',
        views.ProjectUpdateView.as_view(),
        name='edit',
    ),
    path(
        'projects/<int:pk>/people/',
        views.ProjectPeopleView.as_view(),
        name='people',
    ),
    path(
        'projects/<int:pk>/messages/',
        views.ProjectMessageListView.as_view(),
        name='message_list',
    ),
    path(
        'projects/<int:pk>/messages/new/',
        views.ProjectMessageCreateView.as_view(),
        name='message_create',
    ),
    path(
        'projects/<int:pk>/messages/<int:thread_pk>/',
        views.ProjectMessageThreadView.as_view(),
        name='message_thread',
    ),
    path(
        'projects/<int:pk>/messages/<int:thread_pk>/<str:action>/',
        views.ProjectMessageStatusView.as_view(),
        name='message_status',
    ),
    path(
        'projects/<int:pk>/invite-client/',
        views.ClientInviteView.as_view(),
        name='invite_client',
    ),
    path(
        'projects/<int:pk>/invitations/<int:invitation_pk>/resend/',
        views.ProjectInvitationResendView.as_view(),
        name='resend_client_invitation',
    ),
    path(
        'projects/<int:pk>/invitations/<int:invitation_pk>/revoke/',
        views.ProjectInvitationRevokeView.as_view(),
        name='revoke_client_invitation',
    ),
    path(
        'projects/<int:pk>/members/<int:membership_pk>/<str:action>/',
        views.ProjectMemberAccessView.as_view(),
        name='project_member_access',
    ),
    path(
        'invitations/<uuid:token>/',
        views.InvitationAcceptView.as_view(),
        name='accept_invitation',
    ),
    path('companies/', views.CompanyListView.as_view(), name='company_list'),
    path(
        'companies/<slug:slug>/team/',
        views.CompanyTeamView.as_view(),
        name='company_team',
    ),
    path(
        'companies/<slug:slug>/team/invite/',
        views.TeamInviteView.as_view(),
        name='invite_team_member',
    ),
    path(
        'companies/<slug:slug>/team/invitations/<int:invitation_pk>/resend/',
        views.TeamInvitationResendView.as_view(),
        name='resend_team_invitation',
    ),
    path(
        'companies/<slug:slug>/team/invitations/<int:invitation_pk>/revoke/',
        views.TeamInvitationRevokeView.as_view(),
        name='revoke_team_invitation',
    ),
    path(
        'companies/<slug:slug>/team/members/<int:membership_pk>/update/',
        views.TeamMembershipUpdateView.as_view(),
        name='update_team_membership',
    ),
    path(
        'team-invitations/<uuid:token>/',
        views.TeamInvitationAcceptView.as_view(),
        name='accept_team_invitation',
    ),
]
