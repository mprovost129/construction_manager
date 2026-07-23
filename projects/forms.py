from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from .models import ProjectInvitation


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
