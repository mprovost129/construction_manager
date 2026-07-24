from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from .models import (
    ChangeOrder,
    ConversationMessage,
    DocumentDecision,
    FinishSelection,
    OrganizationInvitation,
    OrganizationMembership,
    Project,
    ProjectDocument,
    ProjectInvitation,
    SelectionOption,
)

ALLOWED_DOCUMENT_EXTENSIONS = {
    '.doc',
    '.docx',
    '.jpeg',
    '.jpg',
    '.pdf',
    '.png',
    '.xls',
    '.xlsx',
}


def validate_document_file(uploaded_file):
    extension = Path(uploaded_file.name).suffix.lower()
    if extension not in ALLOWED_DOCUMENT_EXTENSIONS:
        allowed = ', '.join(sorted(ALLOWED_DOCUMENT_EXTENSIONS))
        raise forms.ValidationError(f'Unsupported file type. Allowed types: {allowed}.')
    if uploaded_file.size > settings.DOCUMENT_MAX_UPLOAD_SIZE:
        maximum_mb = settings.DOCUMENT_MAX_UPLOAD_SIZE // (1024 * 1024)
        raise forms.ValidationError(f'File must be {maximum_mb} MB or smaller.')
    return uploaded_file


class ProjectDocumentCreateForm(forms.ModelForm):
    file = forms.FileField()
    version_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
    )

    class Meta:
        model = ProjectDocument
        fields = (
            'title',
            'description',
            'category',
            'client_visible',
            'requires_client_approval',
        )
        widgets = {'description': forms.Textarea(attrs={'rows': 4})}

    def clean_file(self):
        return validate_document_file(self.cleaned_data['file'])


class ProjectDocumentVersionForm(forms.Form):
    file = forms.FileField()
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
    )

    def clean_file(self):
        return validate_document_file(self.cleaned_data['file'])


class DocumentDecisionForm(forms.ModelForm):
    class Meta:
        model = DocumentDecision
        fields = ('decision', 'comment')
        widgets = {
            'decision': forms.RadioSelect,
            'comment': forms.Textarea(attrs={'rows': 4}),
        }


class ChangeOrderForm(forms.ModelForm):
    class Meta:
        model = ChangeOrder
        fields = (
            'title',
            'description',
            'reason',
            'price_delta',
            'cost_delta',
            'schedule_delta_days',
        )
        labels = {
            'price_delta': 'Client price change',
            'cost_delta': 'Estimated project cost change',
            'schedule_delta_days': 'Schedule change (days)',
        }
        help_texts = {
            'price_delta': 'Use a negative amount for a client credit.',
            'cost_delta': 'Internal only. Use a negative amount for a cost reduction.',
            'schedule_delta_days': 'Use a negative number if the change saves time.',
        }
        widgets = {
            'description': forms.Textarea(attrs={'rows': 5}),
            'reason': forms.Textarea(attrs={'rows': 3}),
            'price_delta': forms.NumberInput(attrs={'step': '0.01'}),
            'cost_delta': forms.NumberInput(attrs={'step': '0.01'}),
        }


class ChangeOrderDecisionForm(forms.Form):
    decision = forms.ChoiceField(
        choices=(
            (ChangeOrder.Status.APPROVED, 'Approve'),
            (ChangeOrder.Status.DECLINED, 'Decline'),
        ),
        widget=forms.RadioSelect,
    )
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
    )


class FinishSelectionForm(forms.ModelForm):
    class Meta:
        model = FinishSelection
        fields = (
            'title',
            'description',
            'location',
            'allowance_amount',
            'due_date',
        )
        labels = {'allowance_amount': 'Client allowance'}
        help_texts = {
            'allowance_amount': (
                'Clients will see each option as over, under, or within this allowance.'
            )
        }
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'allowance_amount': forms.NumberInput(attrs={'step': '0.01'}),
            'due_date': forms.DateInput(attrs={'type': 'date'}),
        }


class SelectionOptionForm(forms.ModelForm):
    class Meta:
        model = SelectionOption
        fields = (
            'name',
            'description',
            'price',
            'cost',
            'is_recommended',
            'sort_order',
        )
        labels = {
            'price': 'Client option price',
            'cost': 'Estimated project cost',
            'sort_order': 'Display order',
        }
        help_texts = {
            'cost': 'Internal only and never shown to clients.',
            'sort_order': 'Lower numbers appear first.',
        }
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'price': forms.NumberInput(attrs={'step': '0.01'}),
            'cost': forms.NumberInput(attrs={'step': '0.01'}),
        }


class SelectionDecisionForm(forms.Form):
    option = forms.ModelChoiceField(
        queryset=SelectionOption.objects.none(),
        empty_label=None,
        widget=forms.RadioSelect,
    )
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
    )

    def __init__(self, *args, selection, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['option'].queryset = selection.options.all()


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
