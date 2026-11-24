from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone

from .choice_images import ChoiceGalleryMixin, MultipleImageField
from .message_content import MessageFormMixin
from .models import (
    ChangeOrder,
    ChangeOrderLineItem,
    ConversationMessage,
    CostCode,
    DailyLog,
    DailyLogPhoto,
    DocumentDecision,
    DocumentSignatureRequest,
    Estimate,
    EstimateLineItem,
    FinishSelection,
    Organization,
    OrganizationDisclosure,
    OrganizationInvitation,
    OrganizationMembership,
    Project,
    ProjectCostEntry,
    ProjectDocument,
    ProjectInternalAccess,
    ProjectInvitation,
    ProjectMembership,
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
    version_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    decision = forms.ChoiceField(
        choices=(
            (DocumentDecision.Decision.APPROVED, 'Approve'),
            (DocumentDecision.Decision.DECLINED, 'Decline'),
        ),
        widget=forms.RadioSelect,
    )

    class Meta:
        model = DocumentDecision
        fields = ('decision', 'comment')
        widgets = {
            'comment': forms.Textarea(attrs={'rows': 4}),
        }


class DocumentSignatureRequestForm(forms.ModelForm):
    class Meta:
        model = DocumentSignatureRequest
        fields = ('signer', 'message', 'due_date')
        widgets = {
            'message': forms.Textarea(attrs={'rows': 4}),
            'due_date': forms.DateInput(attrs={'type': 'date'}),
        }
        help_texts = {
            'message': 'Optional instructions shown to the signer and included by email.',
            'due_date': 'Optional. The request remains available after this date but is marked overdue.',
        }

    def __init__(self, *args, project, version, **kwargs):
        super().__init__(*args, **kwargs)
        # ModelForm validation runs before the view can attach these immutable
        # request fields. Seed safe temporary values so model-level signer
        # validation can still verify project membership.
        self.instance.version = version
        self.instance.document_sha256 = '0' * 64
        self.instance.consent_text = 'Pending signature request consent snapshot.'
        already_requested = version.signature_requests.values('signer_id')
        self.fields['signer'].queryset = get_user_model().objects.filter(
            project_memberships__project=project,
            project_memberships__role=OrganizationMembership.Role.CLIENT,
            project_memberships__is_active=True,
            is_active=True,
        ).exclude(pk__in=already_requested).distinct().order_by('email')

    def clean_due_date(self):
        due_date = self.cleaned_data.get('due_date')
        if due_date and due_date < timezone.localdate():
            raise forms.ValidationError('The due date cannot be in the past.')
        return due_date


class DocumentSignatureResponseForm(forms.Form):
    class Action:
        SIGN = 'sign'
        DECLINE = 'decline'

    ACTION_CHOICES = (
        (Action.SIGN, 'Sign document'),
        (Action.DECLINE, 'Decline to sign'),
    )

    action = forms.ChoiceField(choices=ACTION_CHOICES, widget=forms.RadioSelect)
    typed_name = forms.CharField(
        required=False,
        max_length=200,
        label='Your full legal name',
        help_text='Typing your name constitutes your electronic signature.',
    )
    consent = forms.BooleanField(
        required=False,
        label=(
            'I have reviewed this document and agree to use an electronic '
            'signature for this transaction.'
        ),
    )
    decline_reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
        label='Reason for declining',
    )

    def clean(self):
        cleaned_data = super().clean()
        action = cleaned_data.get('action')
        typed_name = cleaned_data.get('typed_name', '').strip()
        decline_reason = cleaned_data.get('decline_reason', '').strip()
        cleaned_data['typed_name'] = typed_name
        cleaned_data['decline_reason'] = decline_reason
        if action == self.Action.SIGN:
            if not typed_name:
                self.add_error('typed_name', 'Enter your full legal name to sign.')
            if not cleaned_data.get('consent'):
                self.add_error('consent', 'Consent is required to sign electronically.')
        elif action == self.Action.DECLINE and not decline_reason:
            self.add_error('decline_reason', 'Explain why you are declining to sign.')
        return cleaned_data


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
            'source_selection',
        )
        labels = {
            'price_delta': 'Client price change',
            'cost_delta': 'Estimated project cost change',
            'schedule_delta_days': 'Schedule change (days)',
            'source_selection': 'Apply a selection credit',
        }
        help_texts = {
            'price_delta': 'Use a negative amount for a client credit.',
            'cost_delta': 'Internal only. Use a negative amount for a cost reduction.',
            'schedule_delta_days': 'Use a negative number if the change saves time.',
            'source_selection': (
                'Optional. Only selections with an unapplied credit are listed.'
            ),
        }
        widgets = {
            'description': forms.Textarea(attrs={'rows': 5}),
            'reason': forms.Textarea(attrs={'rows': 3}),
            'price_delta': forms.NumberInput(attrs={'step': '0.01'}),
            'cost_delta': forms.NumberInput(attrs={'step': '0.01'}),
        }

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['source_selection'].required = False
        credited_selection_ids = ChangeOrder.objects.filter(
            source_selection__isnull=False,
            project=project,
        ).exclude(status=ChangeOrder.Status.VOIDED).exclude(
            pk=self.instance.pk
        ).values_list('source_selection_id', flat=True)
        eligible_selections = project.finish_selections.filter(
            status=FinishSelection.Status.SELECTED,
            credit_disposition=FinishSelection.CreditDisposition.APPLY_ELSEWHERE,
        ).exclude(pk__in=credited_selection_ids)
        self.fields['source_selection'].queryset = eligible_selections
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


class OrganizationTaxSettingsForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ('default_tax_rate',)
        labels = {'default_tax_rate': 'Default tax rate (%)'}
        help_texts = {
            'default_tax_rate': (
                'Applied automatically to new invoice and estimate drafts. '
                'Each draft can still override its own rate.'
            ),
        }
        widgets = {
            'default_tax_rate': forms.NumberInput(
                attrs={'step': '0.001', 'min': '0', 'max': '100'}
            ),
        }


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
    tax_rate = forms.DecimalField(
        required=False,
        min_value=0,
        max_value=100,
        label='Tax rate (%)',
        help_text='Applied to the line-item subtotal. Set to 0 for tax-exempt.',
        widget=forms.NumberInput(attrs={'step': '0.001', 'min': '0', 'max': '100'}),
    )

    class Meta:
        model = Estimate
        fields = ('title', 'description', 'tax_rate', 'required_approvals')
        widgets = {'description': forms.Textarea(attrs={'rows': 5})}

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        if not self.is_bound and not self.instance.pk:
            self.initial.setdefault('tax_rate', organization.default_tax_rate)

    def clean_required_approvals(self):
        return self.cleaned_data['required_approvals'] or 1

    def clean_tax_rate(self):
        tax_rate = self.cleaned_data.get('tax_rate')
        return tax_rate if tax_rate is not None else self.organization.default_tax_rate


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
        label='New category name',
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
            'allowance_mode',
            'allowance_amount',
            'due_date',
        )
        labels = {'allowance_amount': 'Client allowance', 'package': 'Category'}
        help_texts = {
            'allowance_amount': (
                'Clients will see each option as over, under, or within this allowance. '
                'For a standalone credit, use a zero allowance and a negative choice price.'
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
        self.fields['allowance_mode'].required = False
        self.fields['allowance_amount'].required = False
        self.fields['allowance_mode'].label = 'Allowance source'
        self.fields['allowance_amount'].help_text = 'Used only for Own allowance. Zero means the full choice price is additional. No allowance means included in the original contract.'
        self.fields['package'].queryset = project.selection_packages.all()

    def clean(self):
        cleaned_data = super().clean()
        package = cleaned_data.get('package')
        new_package_title = cleaned_data.get('new_package_title', '').strip()
        if package and new_package_title:
            raise forms.ValidationError(
                'Choose an existing package or name a new one, not both.'
            )
        mode = cleaned_data.get('allowance_mode') or self.instance.allowance_mode
        cleaned_data['allowance_mode'] = mode
        if cleaned_data.get('allowance_amount') is None:
            if mode == 'individual':
                self.add_error('allowance_amount', 'Enter the individual allowance.')
            cleaned_data['allowance_amount'] = self.instance.allowance_amount or 0
        if mode == 'section' and (not package or package.allowance_amount is None):
            self.add_error('allowance_mode', 'Choose a category with a shared allowance. Set it up in Manage allowances first.')
        cleaned_data['new_package_title'] = new_package_title
        return cleaned_data


class SelectionOptionForm(ChoiceGalleryMixin, forms.ModelForm):
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
            'image_caption',
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
            'price': 'Use a negative amount for a client credit, e.g. -250.00. The allowance is subtracted from this price to calculate the selection difference.',
            'cost': 'Internal only and never shown to clients. Use a negative amount for a vendor or project cost credit.',
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
            'image': forms.FileInput(attrs={'accept': 'image/png,image/jpeg,image/webp'}),
            'attachment': forms.FileInput(),
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


class DailyLogForm(forms.ModelForm):
    images = MultipleImageField(required=False, label='Progress photos',
        help_text='Choose multiple photos. JPEG, PNG or WebP; up to 20 photos per upload, 10 MB each and 50 MB total. Thumbnails are created automatically. Photos share this update’s visibility.')

    class Meta:
        model = DailyLog
        fields = ('log_date', 'title', 'summary', 'client_visible')
        labels = {
            'log_date': 'Update date',
            'summary': 'Progress update',
            'client_visible': 'Publish to the client dashboard',
        }
        help_texts = {
            'client_visible': (
                'Clients can see this update and its photos only while this is selected.'
            ),
        }
        widgets = {
            'log_date': forms.DateInput(attrs={'type': 'date'}),
            'summary': forms.Textarea(attrs={'rows': 7}),
        }


class DailyLogPhotoForm(forms.ModelForm):
    class Meta:
        model = DailyLogPhoto
        fields = ('image', 'caption', 'sort_order')
        labels = {
            'image': 'Photo',
            'sort_order': 'Display order',
        }

    def clean_image(self):
        return validate_image_file(self.cleaned_data['image'])


class ConversationThreadForm(MessageFormMixin, forms.Form):
    subject = forms.CharField(max_length=200)
    body = forms.CharField(
        label='Message',
        widget=forms.Textarea(attrs={'rows': 6}),
    )


class ConversationReplyForm(MessageFormMixin, forms.ModelForm):
    class Meta:
        model = ConversationMessage
        fields = ('body', 'body_html', 'submission_id')
        labels = {'body': 'Reply'}
        widgets = {'body': forms.Textarea(attrs={'rows': 5})}

    def clean_body(self):
        body = self.cleaned_data['body'].strip()
        return body


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        labels = {'description': 'Internal project notes'}
        help_texts = {'description': 'Visible to company staff only. Share client updates through Project updates.'}
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


class MFARequirementForm(forms.Form):
    mfa_required = forms.BooleanField(required=False, label='Require two-step verification (2FA)',
        help_text='Checked: this person must use an authenticator. Unchecked: optional; an existing authenticator stays on until its owner turns it off in Account security. A requirement from another company still applies.')


class FeatureAccessForm(forms.ModelForm):
    mfa_required = MFARequirementForm.base_fields['mfa_required']
    desktop_manage = forms.BooleanField(required=False, label='Manage QuickBooks connection',
        help_text='Create or reset the shared connection, download its setup file, and disconnect it. Requires View QuickBooks setup.')
    desktop_check = forms.BooleanField(required=False, label='Request QuickBooks connection checks',
        help_text='Queue a company-file check for the connection computer. Financial transfers are not enabled. Requires View QuickBooks setup.')
    accounting_view = forms.BooleanField(required=False, label='View QuickBooks setup',
        help_text='Read company setup details and the accountant checklist.')
    accounting_edit = forms.BooleanField(required=False, label='Update QuickBooks setup',
        help_text='Save setup details and checklist progress. Requires View QuickBooks setup.')
    project_budget_view = forms.BooleanField(required=False, label='View assigned project budgets',
        help_text='See internal costs and profitability on assigned projects. Project assignment is still required.')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('accounting_view', 'accounting_edit', 'project_budget_view', 'desktop_manage', 'desktop_check'):
            if getattr(self.instance, name, None) is None:
                membership = OrganizationMembership(role=getattr(self.instance, 'role', ''))
                self.initial.setdefault(name, membership.feature_allowed(name))
                if self.initial[name] is None:
                    self.initial[name] = membership.feature_allowed(name)

    def clean(self):
        data = super().clean()
        if data.get('accounting_edit') and not data.get('accounting_view'):
            self.add_error('accounting_edit', 'Also select View QuickBooks setup to allow updates.')
        for name in ('desktop_manage', 'desktop_check'):
            if data.get(name) and not data.get('accounting_view'):
                self.add_error(name, 'Also select View QuickBooks setup to grant this permission.')
        return data


class TeamInvitationForm(FeatureAccessForm):
    class Meta:
        model = OrganizationInvitation
        fields = ('email', 'role', 'mfa_required', 'accounting_view', 'accounting_edit', 'desktop_manage', 'desktop_check', 'project_budget_view')
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


class TeamMembershipForm(FeatureAccessForm):
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
        fields = ('role', 'is_active', 'mfa_required', 'accounting_view', 'accounting_edit', 'desktop_manage', 'desktop_check', 'project_budget_view')


class ProjectInternalAccessForm(forms.ModelForm):
    class Meta:
        model = ProjectInternalAccess
        fields = (
            'membership',
            'is_project_manager',
            'can_manage',
            'can_invite_clients',
        )
        labels = {
            'membership': 'Internal user',
            'is_project_manager': 'Assign as a project manager',
            'can_manage': 'Manage project workflows',
            'can_invite_clients': 'Invite and manage clients',
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
        fields = ('client_access_type', 'email')
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
        self.fields['client_access_type'].required = False

    def clean_client_access_type(self):
        return (
            self.cleaned_data.get('client_access_type')
            or ProjectMembership.ClientAccessType.PRIMARY
        )

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        access_type = self.cleaned_data.get(
            'client_access_type',
            ProjectMembership.ClientAccessType.PRIMARY,
        )
        if access_type == ProjectMembership.ClientAccessType.PRIMARY and self.project.project_memberships.filter(
            role=OrganizationMembership.Role.CLIENT,
            client_access_type=ProjectMembership.ClientAccessType.PRIMARY,
            is_active=True,
        ).exists():
            raise forms.ValidationError(
                'This project already has a designated client account holder. '
                'Revoke that access before inviting a replacement.'
            )
        if access_type == ProjectMembership.ClientAccessType.PRIMARY and self.project.invitations.filter(
            role=OrganizationMembership.Role.CLIENT,
            client_access_type=ProjectMembership.ClientAccessType.PRIMARY,
            accepted_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).exclude(email__iexact=email).exists():
            raise forms.ValidationError(
                'A designated client invitation is already pending for this project.'
            )
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


class OrganizationDisclosureForm(forms.ModelForm):
    class Meta:
        model = OrganizationDisclosure
        fields = (
            'title',
            'body',
            'include_on_change_orders',
            'include_on_invoices',
            'is_active',
            'sort_order',
        )
        widgets = {'body': forms.Textarea(attrs={'rows': 6})}


class PublicDocumentVerificationForm(forms.Form):
    human_verified = forms.BooleanField(
        label='I am a person requesting this project document',
        required=True,
    )


class InvitationSignupForm(UserCreationForm):
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)

    class Meta:
        model = get_user_model()
        fields = ('first_name', 'last_name', 'password1', 'password2')
