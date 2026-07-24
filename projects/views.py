from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, FormView, TemplateView

from .access import (
    can_invite_clients,
    can_manage_organization,
    can_manage_project,
    can_use_project_messaging,
    internal_organizations_for_user,
    is_project_client,
    organization_membership_for,
    projects_for_user,
)
from .forms import (
    ClientInvitationForm,
    ConversationReplyForm,
    ConversationThreadForm,
    InvitationSignupForm,
    ProjectForm,
    TeamInvitationForm,
    TeamMembershipForm,
)
from .models import (
    ActivityEvent,
    ConversationMessage,
    ConversationThread,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    Project,
    ProjectInvitation,
    ProjectMembership,
)
from .services import (
    accept_organization_invitation,
    accept_project_invitation,
    record_activity,
    send_message_notifications,
    send_project_invitation,
    send_team_invitation,
)


def managed_project_or_404(user, pk):
    project = get_object_or_404(
        projects_for_user(user).select_related('organization'),
        pk=pk,
    )
    if not can_manage_project(user, project):
        raise PermissionDenied('You cannot manage this project.')
    return project


def messaging_project_or_404(user, pk):
    project = get_object_or_404(
        projects_for_user(user).select_related('organization'),
        pk=pk,
    )
    if not can_use_project_messaging(user, project):
        raise PermissionDenied('You cannot access project messaging.')
    return project


def internal_organization_or_404(user, slug):
    return get_object_or_404(internal_organizations_for_user(user), slug=slug)


def managed_organization_or_404(user, slug):
    organization = internal_organization_or_404(user, slug)
    if not can_manage_organization(user, organization):
        raise PermissionDenied('Only company administrators can manage the team.')
    return organization


class ProjectDetailView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = 'projects/project_detail.html'
    context_object_name = 'project'

    def get_queryset(self):
        return projects_for_user(self.request.user).select_related('organization')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organization_membership = organization_membership_for(
            self.request.user, self.object.organization
        )
        project_membership = self.object.project_memberships.filter(
            user=self.request.user,
            is_active=True,
        ).first()
        is_internal = bool(
            self.request.user.is_superuser
            or (organization_membership and organization_membership.is_internal)
        )
        context.update(
            {
                'organization_membership': organization_membership,
                'project_membership': project_membership,
                'can_manage_project': can_manage_project(
                    self.request.user, self.object
                ),
                'can_invite_clients': can_invite_clients(
                    self.request.user, self.object
                ),
                'can_use_project_messaging': can_use_project_messaging(
                    self.request.user, self.object
                ),
                'can_view_project_financials': bool(
                    self.request.user.is_superuser
                    or (
                        organization_membership
                        and organization_membership.can_view_project_financials
                    )
                ),
                'activity_events': (
                    self.object.activity_events.select_related('actor')[:10]
                    if is_internal
                    else ()
                ),
            }
        )
        return context


class ProjectMessageListView(LoginRequiredMixin, TemplateView):
    template_name = 'projects/message_list.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = messaging_project_or_404(request.user, kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'project': self.project,
                'threads': self.project.conversation_threads.select_related(
                    'created_by'
                ).annotate(message_count=Count('messages')),
                'can_manage_project': can_manage_project(
                    self.request.user, self.project
                ),
            }
        )
        return context


class ProjectMessageCreateView(LoginRequiredMixin, FormView):
    form_class = ConversationThreadForm
    template_name = 'projects/message_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = messaging_project_or_404(request.user, kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.project
        return context

    def form_valid(self, form):
        with transaction.atomic():
            thread = ConversationThread.objects.create(
                project=self.project,
                subject=form.cleaned_data['subject'].strip(),
                created_by=self.request.user,
            )
            message = ConversationMessage(
                thread=thread,
                author=self.request.user,
                body=form.cleaned_data['body'],
            )
            message.full_clean()
            message.save()
            record_activity(
                organization=self.project.organization,
                project=self.project,
                actor=self.request.user,
                event_type=ActivityEvent.Type.MESSAGE_THREAD_CREATED,
                summary=(
                    f'{self.request.user.email} started the conversation '
                    f'"{thread.subject}".'
                ),
                metadata={'thread_id': thread.pk, 'subject': thread.subject},
            )
        send_message_notifications(self.request, message, new_thread=True)
        messages.success(self.request, 'Conversation started.')
        return redirect(
            'projects:message_thread', pk=self.project.pk, thread_pk=thread.pk
        )


class ProjectMessageThreadView(LoginRequiredMixin, View):
    template_name = 'projects/message_thread.html'

    def get_objects(self, request, pk, thread_pk):
        project = messaging_project_or_404(request.user, pk)
        thread = get_object_or_404(
            ConversationThread.objects.select_related('created_by'),
            pk=thread_pk,
            project=project,
        )
        return project, thread

    def render_thread(self, request, project, thread, form=None):
        return render(
            request,
            self.template_name,
            {
                'project': project,
                'thread': thread,
                'thread_messages': thread.messages.select_related('author'),
                'form': form or ConversationReplyForm(),
                'can_manage_project': can_manage_project(request.user, project),
                'viewer_is_client': is_project_client(request.user, project),
            },
        )

    def get(self, request, pk, thread_pk):
        project, thread = self.get_objects(request, pk, thread_pk)
        return self.render_thread(request, project, thread)

    def post(self, request, pk, thread_pk):
        project, thread = self.get_objects(request, pk, thread_pk)
        if thread.status == ConversationThread.Status.CLOSED:
            messages.error(request, 'This conversation is closed. It must be reopened before replying.')
            return redirect(
                'projects:message_thread', pk=project.pk, thread_pk=thread.pk
            )
        form = ConversationReplyForm(request.POST)
        if not form.is_valid():
            return self.render_thread(request, project, thread, form=form)
        with transaction.atomic():
            message = form.save(commit=False)
            message.thread = thread
            message.author = request.user
            message.full_clean()
            message.save()
            ConversationThread.objects.filter(pk=thread.pk).update(
                updated_at=timezone.now()
            )
            record_activity(
                organization=project.organization,
                project=project,
                actor=request.user,
                event_type=ActivityEvent.Type.MESSAGE_SENT,
                summary=f'{request.user.email} replied to "{thread.subject}".',
                metadata={'thread_id': thread.pk, 'subject': thread.subject},
            )
        send_message_notifications(request, message)
        messages.success(request, 'Reply sent.')
        return redirect(
            'projects:message_thread', pk=project.pk, thread_pk=thread.pk
        )


class ProjectMessageStatusView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, pk, thread_pk, action):
        project = managed_project_or_404(request.user, pk)
        thread = get_object_or_404(
            ConversationThread, pk=thread_pk, project=project
        )
        if action not in ('close', 'reopen'):
            raise PermissionDenied('Unknown conversation action.')
        thread.status = (
            ConversationThread.Status.CLOSED
            if action == 'close'
            else ConversationThread.Status.OPEN
        )
        thread.save(update_fields=('status', 'updated_at'))
        record_activity(
            organization=project.organization,
            project=project,
            actor=request.user,
            event_type=ActivityEvent.Type.MESSAGE_THREAD_STATUS_CHANGED,
            summary=f'{request.user.email} {action}d "{thread.subject}".',
            metadata={
                'thread_id': thread.pk,
                'subject': thread.subject,
                'status': thread.status,
            },
        )
        messages.success(request, f'Conversation {action}d.')
        return redirect(
            'projects:message_thread', pk=project.pk, thread_pk=thread.pk
        )


class ProjectCreateView(LoginRequiredMixin, FormView):
    form_class = ProjectForm
    template_name = 'projects/project_form.html'

    def get_organizations(self):
        return internal_organizations_for_user(
            self.request.user, management_only=True
        )

    def dispatch(self, request, *args, **kwargs):
        if not self.get_organizations().exists():
            raise PermissionDenied('You cannot create projects.')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organizations'] = self.get_organizations()
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        organization_id = self.request.GET.get('organization')
        if organization_id and self.get_organizations().filter(pk=organization_id).exists():
            initial['organization'] = organization_id
        elif self.get_organizations().count() == 1:
            initial['organization'] = self.get_organizations().first()
        return initial

    def form_valid(self, form):
        project = form.save(commit=False)
        project.created_by = self.request.user
        project.save()
        record_activity(
            organization=project.organization,
            project=project,
            actor=self.request.user,
            event_type=ActivityEvent.Type.PROJECT_CREATED,
            summary=f'{self.request.user.email} created the project.',
        )
        messages.success(self.request, f'{project.name} was created.')
        return redirect('projects:detail', pk=project.pk)


class ProjectUpdateView(LoginRequiredMixin, FormView):
    form_class = ProjectForm
    template_name = 'projects/project_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = managed_project_or_404(request.user, kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update(
            {
                'instance': self.project,
                'organizations': Organization.objects.filter(
                    pk=self.project.organization_id
                ),
                'lock_organization': True,
            }
        )
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.project
        return context

    def form_valid(self, form):
        changed_fields = list(form.changed_data)
        project = form.save()
        if changed_fields:
            record_activity(
                organization=project.organization,
                project=project,
                actor=self.request.user,
                event_type=ActivityEvent.Type.PROJECT_UPDATED,
                summary=f'{self.request.user.email} updated the project.',
                metadata={'fields': changed_fields},
            )
        messages.success(self.request, f'{project.name} was updated.')
        return redirect('projects:detail', pk=project.pk)


class ProjectPeopleView(LoginRequiredMixin, TemplateView):
    template_name = 'projects/project_people.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = managed_project_or_404(request.user, kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'project': self.project,
                'project_memberships': self.project.project_memberships.select_related(
                    'user'
                ),
                'invitations': self.project.invitations.select_related(
                    'invited_by', 'accepted_by'
                )[:50],
            }
        )
        return context


class ClientInviteView(LoginRequiredMixin, FormView):
    form_class = ClientInvitationForm
    template_name = 'projects/invite_client.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = managed_project_or_404(request.user, kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['project'] = self.project
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.project
        return context

    def form_valid(self, form):
        with transaction.atomic():
            if form.expired_invitation:
                form.expired_invitation.revoked_at = timezone.now()
                form.expired_invitation.save(update_fields=('revoked_at',))
            invitation = form.save(commit=False)
            invitation.project = self.project
            invitation.role = OrganizationMembership.Role.CLIENT
            invitation.invited_by = self.request.user
            invitation.full_clean()
            invitation.save()
            record_activity(
                organization=self.project.organization,
                project=self.project,
                actor=self.request.user,
                event_type=ActivityEvent.Type.CLIENT_INVITED,
                summary=f'{self.request.user.email} invited {invitation.email}.',
                metadata={'email': invitation.email},
            )
        send_project_invitation(self.request, invitation)
        messages.success(self.request, f'Invitation sent to {invitation.email}.')
        return redirect('projects:people', pk=self.project.pk)


class ProjectInvitationResendView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, pk, invitation_pk):
        project = managed_project_or_404(request.user, pk)
        old_invitation = get_object_or_404(
            ProjectInvitation, pk=invitation_pk, project=project
        )
        if old_invitation.accepted_at:
            messages.error(request, 'Accepted invitations cannot be resent.')
            return redirect('projects:people', pk=project.pk)
        with transaction.atomic():
            old_invitation.revoked_at = timezone.now()
            old_invitation.save(update_fields=('revoked_at',))
            invitation = ProjectInvitation.objects.create(
                project=project,
                email=old_invitation.email,
                role=old_invitation.role,
                invited_by=request.user,
            )
            record_activity(
                organization=project.organization,
                project=project,
                actor=request.user,
                event_type=ActivityEvent.Type.CLIENT_INVITE_RESENT,
                summary=f'{request.user.email} resent the invitation to {invitation.email}.',
                metadata={'email': invitation.email},
            )
        send_project_invitation(request, invitation)
        messages.success(request, f'Invitation resent to {invitation.email}.')
        return redirect('projects:people', pk=project.pk)


class ProjectInvitationRevokeView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, pk, invitation_pk):
        project = managed_project_or_404(request.user, pk)
        invitation = get_object_or_404(
            ProjectInvitation, pk=invitation_pk, project=project
        )
        if invitation.accepted_at:
            messages.error(request, 'Accepted invitations cannot be revoked.')
        elif invitation.revoked_at:
            messages.info(request, 'That invitation is already revoked.')
        else:
            invitation.revoked_at = timezone.now()
            invitation.save(update_fields=('revoked_at',))
            record_activity(
                organization=project.organization,
                project=project,
                actor=request.user,
                event_type=ActivityEvent.Type.CLIENT_INVITE_REVOKED,
                summary=f'{request.user.email} revoked the invitation to {invitation.email}.',
                metadata={'email': invitation.email},
            )
            messages.success(request, f'Invitation to {invitation.email} was revoked.')
        return redirect('projects:people', pk=project.pk)


class ProjectMemberAccessView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, pk, membership_pk, action):
        project = managed_project_or_404(request.user, pk)
        membership = get_object_or_404(
            ProjectMembership.objects.select_related('user'),
            pk=membership_pk,
            project=project,
        )
        if action not in ('revoke', 'restore'):
            raise PermissionDenied('Unknown access action.')
        active = action == 'restore'
        membership.is_active = active
        membership.save(update_fields=('is_active',))
        event_type = (
            ActivityEvent.Type.CLIENT_ACCESS_RESTORED
            if active
            else ActivityEvent.Type.CLIENT_ACCESS_REVOKED
        )
        verb = 'restored' if active else 'revoked'
        record_activity(
            organization=project.organization,
            project=project,
            actor=request.user,
            event_type=event_type,
            summary=f'{request.user.email} {verb} access for {membership.user.email}.',
            metadata={'email': membership.user.email},
        )
        messages.success(request, f'Project access was {verb}.')
        return redirect('projects:people', pk=project.pk)


class CompanyListView(LoginRequiredMixin, TemplateView):
    template_name = 'projects/company_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['organizations'] = internal_organizations_for_user(self.request.user)
        return context


class CompanyTeamView(LoginRequiredMixin, TemplateView):
    template_name = 'projects/company_team.html'

    def dispatch(self, request, *args, **kwargs):
        self.organization = internal_organization_or_404(
            request.user, kwargs['slug']
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'organization': self.organization,
                'can_manage_team': can_manage_organization(
                    self.request.user, self.organization
                ),
                'team_memberships': self.organization.memberships.filter(
                    role__in=OrganizationMembership.INTERNAL_ROLES
                ).select_related('user'),
                'team_invitations': self.organization.team_invitations.select_related(
                    'invited_by', 'accepted_by'
                )[:50],
                'activity_events': self.organization.activity_events.filter(
                    project__isnull=True
                ).select_related('actor')[:20],
            }
        )
        return context


class TeamInviteView(LoginRequiredMixin, FormView):
    form_class = TeamInvitationForm
    template_name = 'projects/invite_team_member.html'

    def dispatch(self, request, *args, **kwargs):
        self.organization = managed_organization_or_404(
            request.user, kwargs['slug']
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organization'] = self.organization
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['organization'] = self.organization
        return context

    def form_valid(self, form):
        with transaction.atomic():
            if form.expired_invitation:
                form.expired_invitation.revoked_at = timezone.now()
                form.expired_invitation.save(update_fields=('revoked_at',))
            invitation = form.save(commit=False)
            invitation.organization = self.organization
            invitation.invited_by = self.request.user
            invitation.full_clean()
            invitation.save()
            record_activity(
                organization=self.organization,
                actor=self.request.user,
                event_type=ActivityEvent.Type.TEAM_INVITED,
                summary=(
                    f'{self.request.user.email} invited {invitation.email} as '
                    f'{invitation.get_role_display()}.'
                ),
                metadata={'email': invitation.email, 'role': invitation.role},
            )
        send_team_invitation(self.request, invitation)
        messages.success(self.request, f'Invitation sent to {invitation.email}.')
        return redirect('projects:company_team', slug=self.organization.slug)


class TeamInvitationResendView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, slug, invitation_pk):
        organization = managed_organization_or_404(request.user, slug)
        old_invitation = get_object_or_404(
            OrganizationInvitation,
            pk=invitation_pk,
            organization=organization,
        )
        if old_invitation.accepted_at:
            messages.error(request, 'Accepted invitations cannot be resent.')
            return redirect('projects:company_team', slug=organization.slug)
        with transaction.atomic():
            old_invitation.revoked_at = timezone.now()
            old_invitation.save(update_fields=('revoked_at',))
            invitation = OrganizationInvitation.objects.create(
                organization=organization,
                email=old_invitation.email,
                role=old_invitation.role,
                invited_by=request.user,
            )
            record_activity(
                organization=organization,
                actor=request.user,
                event_type=ActivityEvent.Type.TEAM_INVITE_RESENT,
                summary=f'{request.user.email} resent the invitation to {invitation.email}.',
                metadata={'email': invitation.email, 'role': invitation.role},
            )
        send_team_invitation(request, invitation)
        messages.success(request, f'Invitation resent to {invitation.email}.')
        return redirect('projects:company_team', slug=organization.slug)


class TeamInvitationRevokeView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, slug, invitation_pk):
        organization = managed_organization_or_404(request.user, slug)
        invitation = get_object_or_404(
            OrganizationInvitation,
            pk=invitation_pk,
            organization=organization,
        )
        if invitation.accepted_at:
            messages.error(request, 'Accepted invitations cannot be revoked.')
        elif invitation.revoked_at:
            messages.info(request, 'That invitation is already revoked.')
        else:
            invitation.revoked_at = timezone.now()
            invitation.save(update_fields=('revoked_at',))
            record_activity(
                organization=organization,
                actor=request.user,
                event_type=ActivityEvent.Type.TEAM_INVITE_REVOKED,
                summary=f'{request.user.email} revoked the invitation to {invitation.email}.',
                metadata={'email': invitation.email},
            )
            messages.success(request, f'Invitation to {invitation.email} was revoked.')
        return redirect('projects:company_team', slug=organization.slug)


class TeamMembershipUpdateView(LoginRequiredMixin, View):
    http_method_names = ('post',)

    def post(self, request, slug, membership_pk):
        organization = managed_organization_or_404(request.user, slug)
        membership = get_object_or_404(
            OrganizationMembership.objects.select_related('user'),
            pk=membership_pk,
            organization=organization,
            role__in=OrganizationMembership.INTERNAL_ROLES,
        )
        if membership.user_id == request.user.id:
            messages.error(request, 'You cannot change your own role or access.')
            return redirect('projects:company_team', slug=organization.slug)

        old_role = membership.role
        old_active = membership.is_active
        form = TeamMembershipForm(request.POST, instance=membership)
        if not form.is_valid():
            messages.error(request, 'Choose a valid team role and access status.')
            return redirect('projects:company_team', slug=organization.slug)

        new_role = form.cleaned_data['role']
        new_active = form.cleaned_data['is_active']
        removing_admin = old_role == OrganizationMembership.Role.ADMIN and (
            new_role != OrganizationMembership.Role.ADMIN or not new_active
        )
        if removing_admin:
            other_admins = organization.memberships.filter(
                role=OrganizationMembership.Role.ADMIN,
                is_active=True,
            ).exclude(pk=membership.pk)
            if not other_admins.exists():
                messages.error(request, 'A company must retain at least one active admin.')
                return redirect('projects:company_team', slug=organization.slug)

        membership = form.save()
        if old_role != membership.role:
            record_activity(
                organization=organization,
                actor=request.user,
                event_type=ActivityEvent.Type.TEAM_ROLE_CHANGED,
                summary=(
                    f'{request.user.email} changed {membership.user.email} from '
                    f'{OrganizationMembership.Role(old_role).label} to '
                    f'{membership.get_role_display()}.'
                ),
                metadata={
                    'email': membership.user.email,
                    'old_role': old_role,
                    'new_role': membership.role,
                },
            )
        if old_active != membership.is_active:
            status = 'restored' if membership.is_active else 'deactivated'
            record_activity(
                organization=organization,
                actor=request.user,
                event_type=ActivityEvent.Type.TEAM_ACCESS_CHANGED,
                summary=f'{request.user.email} {status} access for {membership.user.email}.',
                metadata={'email': membership.user.email, 'active': membership.is_active},
            )
        messages.success(request, f'{membership.user.email} was updated.')
        return redirect('projects:company_team', slug=organization.slug)


class InvitationAcceptView(View):
    template_name = 'projects/accept_invitation.html'

    def get_invitation(self, token):
        return get_object_or_404(
            ProjectInvitation.objects.select_related('project__organization'),
            token=token,
        )

    def invalid_response(self, request, invitation):
        return render(
            request,
            'projects/invitation_invalid.html',
            {'invitation': invitation},
            status=410,
        )

    def accept_authenticated_user(self, request, invitation):
        if request.user.email.casefold() != invitation.email.casefold():
            return render(
                request,
                'projects/invitation_email_mismatch.html',
                {'invitation': invitation},
                status=403,
            )
        try:
            project = accept_project_invitation(invitation, request.user)
        except ValidationError as error:
            return render(
                request,
                'projects/invitation_invalid.html',
                {'invitation': invitation, 'error': error},
                status=409,
            )
        messages.success(request, f'You now have access to {project.name}.')
        return redirect('projects:detail', pk=project.pk)

    def get(self, request, token):
        invitation = self.get_invitation(token)
        if not invitation.is_valid:
            return self.invalid_response(request, invitation)
        if request.user.is_authenticated:
            if request.user.email.casefold() != invitation.email.casefold():
                return render(
                    request,
                    'projects/invitation_email_mismatch.html',
                    {'invitation': invitation},
                    status=403,
                )
            return render(
                request,
                self.template_name,
                {
                    'invitation': invitation,
                    'existing_user': True,
                    'authenticated_match': True,
                },
            )

        existing_user = get_user_model().objects.filter(
            email__iexact=invitation.email
        ).exists()
        return render(
            request,
            self.template_name,
            {
                'invitation': invitation,
                'existing_user': existing_user,
                'form': None if existing_user else InvitationSignupForm(),
            },
        )

    def post(self, request, token):
        invitation = self.get_invitation(token)
        if not invitation.is_valid:
            return self.invalid_response(request, invitation)
        if request.user.is_authenticated:
            return self.accept_authenticated_user(request, invitation)
        if get_user_model().objects.filter(email__iexact=invitation.email).exists():
            return redirect(
                f"{reverse('login')}?next="
                f"{reverse('projects:accept_invitation', args=(invitation.token,))}"
            )

        form = InvitationSignupForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    'invitation': invitation,
                    'existing_user': False,
                    'form': form,
                },
            )

        user = form.save(commit=False)
        user.email = invitation.email
        user.full_clean()
        user.save()
        project = accept_project_invitation(invitation, user)
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        messages.success(request, f'Welcome. You now have access to {project.name}.')
        return redirect('projects:detail', pk=project.pk)


class TeamInvitationAcceptView(View):
    template_name = 'projects/accept_team_invitation.html'

    def get_invitation(self, token):
        return get_object_or_404(
            OrganizationInvitation.objects.select_related('organization'),
            token=token,
        )

    def invalid_response(self, request, invitation, *, error=None, status=410):
        return render(
            request,
            'projects/team_invitation_invalid.html',
            {'invitation': invitation, 'error': error},
            status=status,
        )

    def accept_authenticated_user(self, request, invitation):
        if request.user.email.casefold() != invitation.email.casefold():
            return render(
                request,
                'projects/team_invitation_email_mismatch.html',
                {'invitation': invitation},
                status=403,
            )
        try:
            organization = accept_organization_invitation(invitation, request.user)
        except ValidationError as error:
            return self.invalid_response(
                request, invitation, error=error, status=409
            )
        messages.success(request, f'You joined {organization.name}.')
        return redirect('projects:company_team', slug=organization.slug)

    def get(self, request, token):
        invitation = self.get_invitation(token)
        if not invitation.is_valid:
            return self.invalid_response(request, invitation)
        if request.user.is_authenticated:
            if request.user.email.casefold() != invitation.email.casefold():
                return render(
                    request,
                    'projects/team_invitation_email_mismatch.html',
                    {'invitation': invitation},
                    status=403,
                )
            return render(
                request,
                self.template_name,
                {'invitation': invitation, 'authenticated_match': True},
            )
        existing_user = get_user_model().objects.filter(
            email__iexact=invitation.email
        ).exists()
        return render(
            request,
            self.template_name,
            {
                'invitation': invitation,
                'existing_user': existing_user,
                'form': None if existing_user else InvitationSignupForm(),
            },
        )

    def post(self, request, token):
        invitation = self.get_invitation(token)
        if not invitation.is_valid:
            return self.invalid_response(request, invitation)
        if request.user.is_authenticated:
            return self.accept_authenticated_user(request, invitation)
        if get_user_model().objects.filter(email__iexact=invitation.email).exists():
            return redirect(
                f"{reverse('login')}?next="
                f"{reverse('projects:accept_team_invitation', args=(invitation.token,))}"
            )
        form = InvitationSignupForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    'invitation': invitation,
                    'existing_user': False,
                    'form': form,
                },
            )
        user = form.save(commit=False)
        user.email = invitation.email
        user.full_clean()
        user.save()
        organization = accept_organization_invitation(invitation, user)
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        messages.success(request, f'Welcome to {organization.name}.')
        return redirect('projects:company_team', slug=organization.slug)
