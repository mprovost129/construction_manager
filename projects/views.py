from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, FormView

from .access import (
    can_invite_clients,
    organization_membership_for,
    projects_for_user,
)
from .forms import ClientInvitationForm, InvitationSignupForm
from .models import OrganizationMembership, Project, ProjectInvitation
from .services import accept_project_invitation


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
        context.update(
            {
                'organization_membership': organization_membership,
                'project_membership': project_membership,
                'can_invite_clients': can_invite_clients(
                    self.request.user, self.object
                ),
                'can_view_project_financials': bool(
                    self.request.user.is_superuser
                    or (
                        organization_membership
                        and organization_membership.can_view_project_financials
                    )
                ),
            }
        )
        return context


class ClientInviteView(LoginRequiredMixin, FormView):
    form_class = ClientInvitationForm
    template_name = 'projects/invite_client.html'

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(
            projects_for_user(request.user).select_related('organization'),
            pk=kwargs['pk'],
        )
        if not can_invite_clients(request.user, self.project):
            raise PermissionDenied('You cannot invite customers to this project.')
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

        accept_url = self.request.build_absolute_uri(
            reverse('projects:accept_invitation', args=(invitation.token,))
        )
        send_mail(
            subject=f'You are invited to {self.project.name}',
            message=(
                f'You have been invited to access {self.project.name} in the '
                f'{self.project.organization.name} customer portal.\n\n'
                f'Accept your invitation: {accept_url}\n\n'
                'This invitation expires in 7 days.'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[invitation.email],
        )
        messages.success(
            self.request,
            f'Invitation sent to {invitation.email}.',
        )
        return redirect('projects:detail', pk=self.project.pk)


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
