from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from .models import (
    ConversationMessage,
    OrganizationInvitation,
    OrganizationMembership,
    Project,
    ProjectInvitation,
)


class ConversationThreadForm(forms.Form):
    subject = forms.CharField(max_length=200)
    body = forms.CharField(
        label='Message',
        widget=forms.Textarea(attrs={'rows': 6}),
    )


class ConversationReplyForm(forms.ModelForm):
    class Meta:
        model = ConversationMessage
        fields = ('body',)
        labels = {'body': 'Reply'}
        widgets = {'body': forms.Textarea(attrs={'rows': 5})}

    def clean_body(self):
        body = self.cleaned_data['body'].strip()
        if not body:
            raise forms.ValidationError('Reply cannot be blank.')
        return body


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = (
            'organization',
            'name',
            'code',
            'description',
            'status',
            'start_date',
            'target_completion_date',
        )
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'target_completion_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, organizations, lock_organization=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['organization'].queryset = organizations
        self.fields['organization'].disabled = lock_organization


class TeamInvitationForm(forms.ModelForm):
    class Meta:
        model = OrganizationInvitation
        fields = ('email', 'role')
        widgets = {
            'email': forms.EmailInput(
                attrs={'autocomplete': 'email', 'placeholder': 'team@example.com'}
            ),
        }

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.expired_invitation = None

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        user = get_user_model().objects.filter(email__iexact=email).first()
        if user and self.organization.memberships.filter(
            user=user,
            role__in=OrganizationMembership.INTERNAL_ROLES,
            is_active=True,
        ).exists():
            raise forms.ValidationError('This person is already an active team member.')
        pending = self.organization.team_invitations.filter(
            email__iexact=email,
            accepted_at__isnull=True,
            revoked_at__isnull=True,
        ).first()
        if pending:
            if pending.is_valid:
                raise forms.ValidationError(
                    'A pending team invitation already exists for this email.'
                )
            self.expired_invitation = pending
        return email


class TeamMembershipForm(forms.ModelForm):
    role = forms.ChoiceField(
        choices=(
            (
                OrganizationMembership.Role.ADMIN,
                OrganizationMembership.Role.ADMIN.label,
            ),
            (
                OrganizationMembership.Role.STAFF,
                OrganizationMembership.Role.STAFF.label,
            ),
            (
                OrganizationMembership.Role.ACCOUNTANT,
                OrganizationMembership.Role.ACCOUNTANT.label,
            ),
        )
    )

    class Meta:
        model = OrganizationMembership
        fields = ('role', 'is_active')


class ClientInvitationForm(forms.ModelForm):
    class Meta:
        model = ProjectInvitation
        fields = ('email',)
        widgets = {
            'email': forms.EmailInput(
                attrs={'autocomplete': 'email', 'placeholder': 'customer@example.com'}
            ),
        }

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        self.expired_invitation = None
        self.fields['email'].label = 'Customer email'

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        user = get_user_model().objects.filter(email__iexact=email).first()
        if user and self.project.project_memberships.filter(user=user).exists():
            raise forms.ValidationError('This customer already has access to the project.')
        pending_invitation = self.project.invitations.filter(
            email__iexact=email,
            accepted_at__isnull=True,
            revoked_at__isnull=True,
        ).first()
        if pending_invitation:
            if pending_invitation.is_valid:
                raise forms.ValidationError(
                    'A pending invitation already exists for this email.'
                )
            self.expired_invitation = pending_invitation
        return email


class InvitationSignupForm(UserCreationForm):
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)

    class Meta:
        model = get_user_model()
        fields = ('first_name', 'last_name', 'password1', 'password2')
