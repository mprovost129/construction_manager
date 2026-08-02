from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone

from .models import (
    ChangeOrder,
    ChangeOrderLineItem,
    ConversationMessage,
    CostCode,
    DocumentDecision,
    Estimate,
    EstimateLineItem,
    FinishSelection,
    OrganizationInvitation,
    OrganizationMembership,
    Project,
    ProjectCostEntry,
    ProjectDocument,
    ProjectInternalAccess,
    ProjectInvitation,
    ScheduleMilestone,
    SelectionCustomRequest,
    SelectionOption,
    SelectionPackage,
)

ALLOWED_DOCUMENT_EXTENSIONS = {
    '.doc',
    '.docx',
    '.csv',
    '.heic',
    '.jpeg',
    '.jpg',
    '.pdf',
    '.png',
    '.ppt',
    '.pptx',
    '.txt',
    '.webp',
    '.xls',
    '.xlsx',
}

ALLOWED_IMAGE_EXTENSIONS = {'.heic', '.jpeg', '.jpg', '.png', '.webp'}


def validate_document_file(uploaded_file):
    extension = Path(uploaded_file.name).suffix.lower()
    if extension not in ALLOWED_DOCUMENT_EXTENSIONS:
        allowed = ', '.join(sorted(ALLOWED_DOCUMENT_EXTENSIONS))
        raise forms.ValidationError(f'Unsupported file type. Allowed types: {allowed}.')
    if uploaded_file.size > settings.DOCUMENT_MAX_UPLOAD_SIZE:
        maximum_mb = settings.DOCUMENT_MAX_UPLOAD_SIZE // (1024 * 1024)
        raise forms.ValidationError(f'File must be {maximum_mb} MB or smaller.')
    return uploaded_file


def validate_image_file(uploaded_file):
    extension = Path(uploaded_file.name).suffix.lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        allowed = ', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))
        raise forms.ValidationError(f'Unsupported image type. Allowed types: {allowed}.')
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
    required_approvals = forms.IntegerField(
        required=False,
        min_value=1,
        initial=1,
        label='Required client approvals',
        help_text='Number of distinct client approvals needed when approval is required.',
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

    def clean_required_approvals(self):
        return self.cleaned_data['required_approvals'] or 1


class ProjectDocumentVersionForm(forms.Form):
    file = forms.FileField()
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
    )

    def clean_file(self):
        return validate_document_file(self.cleaned_data['file'])


class ClientProjectUploadForm(forms.Form):
    title = forms.CharField(max_length=200)
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
    )
    file = forms.FileField()

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
    required_approvals = forms.IntegerField(
        required=False,
        min_value=1,
        initial=1,
        label='Required client approvals',
        help_text='Number of distinct client approvals needed before this is approved.',
    )

    class Meta:
        model = ChangeOrder
        fields = (
            'title',
            'description',
            'reason',
            'price_delta',
            'cost_delta',
            'schedule_delta_days',
            'required_approvals',
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.line_items.exists():
            for field_name in ('price_delta', 'cost_delta'):
                self.fields[field_name].disabled = True
                self.fields[field_name].help_text = (
                    'Computed automatically from line items below.'
                )

    def clean_required_approvals(self):
        return self.cleaned_data['required_approvals'] or 1


class ChangeOrderLineItemForm(forms.ModelForm):
    class Meta:
        model = ChangeOrderLineItem
        fields = (
            'category',
            'cost_code',
            'description',
            'quantity',
            'unit_price',
            'unit_cost',
            'sort_order',
        )
        labels = {
            'unit_price': 'Client unit price',
            'unit_cost': 'Estimated unit cost',
            'cost_code': 'Cost code',
            'sort_order': 'Display order',
        }
        help_texts = {
            'unit_cost': 'Internal only and never shown to clients.',
            'cost_code': 'Optional job-costing code.',
            'sort_order': 'Lower numbers appear first.',
        }
        widgets = {
            'quantity': forms.NumberInput(attrs={'step': '0.01'}),
            'unit_price': forms.NumberInput(attrs={'step': '0.01'}),
            'unit_cost': forms.NumberInput(attrs={'step': '0.01'}),
        }

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cost_code'].queryset = organization.cost_codes.filter(is_active=True)


class ChangeOrderVoidForm(forms.Form):
    reason = forms.CharField(
        label='Reason for voiding',
        widget=forms.Textarea(attrs={'rows': 3}),
    )


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


class CostCodeForm(forms.ModelForm):
    class Meta:
        model = CostCode
        fields = ('code', 'name', 'description', 'is_active')
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization

    def clean_code(self):
        code = self.cleaned_data['code'].strip()
        existing = self.organization.cost_codes.filter(code__iexact=code)
        if self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError('This company already has a cost code with that code.')
        return code


class EstimateForm(forms.ModelForm):
    required_approvals = forms.IntegerField(
        required=False,
        min_value=1,
        initial=1,
        label='Required client approvals',
        help_text='Number of distinct client approvals needed before this is approved.',
    )

    class Meta:
        model = Estimate
        fields = ('title', 'description', 'required_approvals')
        widgets = {'description': forms.Textarea(attrs={'rows': 5})}

    def clean_required_approvals(self):
        return self.cleaned_data['required_approvals'] or 1


class EstimateLineItemForm(forms.ModelForm):
    class Meta:
        model = EstimateLineItem
        fields = (
            'category',
            'cost_code',
            'description',
            'quantity',
            'unit_price',
            'unit_cost',
            'sort_order',
        )
        labels = {
            'unit_price': 'Client unit price',
            'unit_cost': 'Estimated unit cost',
            'cost_code': 'Cost code',
            'sort_order': 'Display order',
        }
        help_texts = {
            'unit_cost': 'Internal only and never shown to clients.',
            'cost_code': 'Optional job-costing code.',
            'sort_order': 'Lower numbers appear first.',
        }
        widgets = {
            'quantity': forms.NumberInput(attrs={'step': '0.01'}),
            'unit_price': forms.NumberInput(attrs={'step': '0.01'}),
            'unit_cost': forms.NumberInput(attrs={'step': '0.01'}),
        }

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cost_code'].queryset = organization.cost_codes.filter(is_active=True)


class EstimateVoidForm(forms.Form):
    reason = forms.CharField(
        label='Reason for voiding',
        widget=forms.Textarea(attrs={'rows': 3}),
    )


class EstimateDecisionForm(forms.Form):
    decision = forms.ChoiceField(
        choices=(
            (Estimate.Status.APPROVED, 'Approve'),
            (Estimate.Status.DECLINED, 'Decline'),
        ),
        widget=forms.RadioSelect,
    )
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
    )


class ProjectCostEntryForm(forms.ModelForm):
    class Meta:
        model = ProjectCostEntry
        fields = (
            'category',
            'cost_code',
            'description',
            'amount',
            'incurred_date',
            'note',
        )
        labels = {'cost_code': 'Cost code'}
        widgets = {
            'amount': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
            'incurred_date': forms.DateInput(attrs={'type': 'date'}),
            'note': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cost_code'].queryset = organization.cost_codes.filter(is_active=True)
        if not self.is_bound:
            self.initial.setdefault('incurred_date', timezone.localdate())


class SelectionPackageForm(forms.ModelForm):
    class Meta:
        model = SelectionPackage
        fields = ('title', 'description')
        widgets = {'description': forms.Textarea(attrs={'rows': 4})}


class FinishSelectionForm(forms.ModelForm):
    new_package_title = forms.CharField(
        required=False,
        label='New package name',
        help_text=(
            'Group this choice with others due together, e.g. "Kitchen." '
            'Leave blank to keep it standalone.'
        ),
    )

    class Meta:
        model = FinishSelection
        fields = (
            'package',
            'title',
            'description',
            'location',
            'allowance_amount',
            'due_date',
        )
        labels = {'allowance_amount': 'Client allowance', 'package': 'Existing package'}
        help_texts = {
            'allowance_amount': (
                'Clients will see each option as over, under, or within this allowance.'
            ),
            'package': 'Choose an existing package instead of naming a new one below.',
        }
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'allowance_amount': forms.NumberInput(attrs={'step': '0.01'}),
            'due_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        self.fields['package'].queryset = project.selection_packages.all()

    def clean(self):
        cleaned_data = super().clean()
        package = cleaned_data.get('package')
        new_package_title = cleaned_data.get('new_package_title', '').strip()
        if package and new_package_title:
            raise forms.ValidationError(
                'Choose an existing package or name a new one, not both.'
            )
        cleaned_data['new_package_title'] = new_package_title
        return cleaned_data


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
            'vendor_name',
            'product_url',
            'specification',
            'lead_time',
            'image',
            'attachment',
        )
        labels = {
            'price': 'Client option price',
            'cost': 'Estimated project cost',
            'sort_order': 'Display order',
            'vendor_name': 'Vendor',
            'product_url': 'Product link',
        }
        help_texts = {
            'cost': 'Internal only and never shown to clients.',
            'sort_order': 'Lower numbers appear first.',
            'product_url': "Link to the vendor's product page.",
            'lead_time': 'Free text, e.g. "6-8 weeks" or "In stock."',
            'image': 'Photo of this option shown to the client.',
            'attachment': 'Optional spec sheet or other document.',
        }
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'specification': forms.Textarea(attrs={'rows': 4}),
            'price': forms.NumberInput(attrs={'step': '0.01'}),
            'cost': forms.NumberInput(attrs={'step': '0.01'}),
        }

    def clean_image(self):
        image = self.cleaned_data.get('image')
        if image and hasattr(image, 'content_type'):
            return validate_image_file(image)
        return image

    def clean_attachment(self):
        attachment = self.cleaned_data.get('attachment')
        if attachment and hasattr(attachment, 'content_type'):
            return validate_document_file(attachment)
        return attachment


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


class SelectionCustomRequestForm(forms.ModelForm):
    class Meta:
        model = SelectionCustomRequest
        fields = ('description', 'target_price')
        labels = {'target_price': 'Target price (optional)'}
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'target_price': forms.NumberInput(attrs={'step': '0.01'}),
        }


class SelectionCreditDispositionForm(forms.Form):
    credit_disposition = forms.ChoiceField(
        choices=[
            choice
            for choice in FinishSelection.CreditDisposition.choices
            if choice[0] != FinishSelection.CreditDisposition.UNDETERMINED
        ],
        widget=forms.RadioSelect,
        label='Credit disposition',
    )


class ScheduleMilestoneForm(forms.ModelForm):
    class Meta:
        model = ScheduleMilestone
        fields = (
            'title',
            'description',
            'start_date',
            'end_date',
            'status',
            'internal_notes',
            'sort_order',
        )
        labels = {
            'sort_order': 'Display order for the same start date',
        }
        help_texts = {
            'description': 'Visible to assigned internal project users.',
            'internal_notes': 'Internal planning notes.',
        }
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'internal_notes': forms.Textarea(attrs={'rows': 4}),
        }


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
                OrganizationMembership.Role.MANAGER,
                OrganizationMembership.Role.MANAGER.label,
            ),
            (
                OrganizationMembership.Role.PROJECT_MANAGER,
                OrganizationMembership.Role.PROJECT_MANAGER.label,
            ),
            (
                OrganizationMembership.Role.STAFF,
                OrganizationMembership.Role.STAFF.label,
            ),
            (
                OrganizationMembership.Role.OFFICE_MANAGER,
                OrganizationMembership.Role.OFFICE_MANAGER.label,
            ),
            (
                OrganizationMembership.Role.REAL_ESTATE_AGENT,
                OrganizationMembership.Role.REAL_ESTATE_AGENT.label,
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


class ProjectInternalAccessForm(forms.ModelForm):
    class Meta:
        model = ProjectInternalAccess
        fields = (
            'membership',
            'can_manage',
            'can_invite_clients',
            'receives_notifications',
        )
        labels = {
            'membership': 'Internal user',
            'can_manage': 'Manage project workflows',
            'can_invite_clients': 'Invite and manage clients',
            'receives_notifications': 'Receive project email notifications',
        }

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        self.fields['membership'].queryset = (
            project.organization.memberships.filter(
                is_active=True,
                role__in=OrganizationMembership.INTERNAL_ROLES,
            )
            .exclude(role=OrganizationMembership.Role.ADMIN)
            .select_related('user')
            .order_by('user__email')
        )

    def clean_membership(self):
        membership = self.cleaned_data['membership']
        if membership.organization_id != self.project.organization_id:
            raise forms.ValidationError('Choose a team member from this company.')
        if not membership.is_internal:
            raise forms.ValidationError('Choose an internal team member.')
        return membership


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
