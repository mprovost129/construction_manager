from django import forms

from projects.models import CostCode, Project

from .models import QuickBooksConnection, QuickBooksDesktopOnboarding


class QuickBooksDesktopOnboardingForm(forms.ModelForm):
    revision = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['revision'] = self.instance.revision

    class Meta:
        model = QuickBooksDesktopOnboarding
        fields = ('edition_year', 'hosting', 'workstation', 'company_file_count',
                  'setup_contact', 'customer_structure', 'item_tax_notes', 'numbering_credits',
                  'operator_ready', 'storage_reviewed', 'backup_ready', 'test_company_ready',
                  'existing_apps_reviewed', 'connector_installed')
        widgets = {name: forms.Textarea(attrs={'rows': 3}) for name in (
            'customer_structure', 'item_tax_notes', 'numbering_credits'
        )}
        help_texts = {
            'edition_year': 'For example: QuickBooks Enterprise 2024. Leave blank until confirmed.',
            'workstation': 'Use a computer name or provider name, without passwords or company-file paths.',
            'setup_contact': 'Work email of the person coordinating setup.',
            'customer_structure': 'Describe how an existing construction project is represented in QuickBooks.',
            'item_tax_notes': 'Identify the items and accounts used for invoice lines and how sales tax is handled.',
            'numbering_credits': 'Who assigns invoice numbers? Where are credits created, and who reviews them?',
        }

    def clean_company_file_count(self):
        count = self.cleaned_data['company_file_count']
        if count is not None and count < 1:
            raise forms.ValidationError('Enter at least one company file, or leave this blank.')
        return count

class QuickBooksProjectCustomerMappingForm(forms.Form):
    project = forms.ModelChoiceField(queryset=Project.objects.none())
    connection = forms.ModelChoiceField(queryset=QuickBooksConnection.objects.none())
    quickbooks_customer_id = forms.CharField(
        max_length=50,
        label='QuickBooks customer ID',
        help_text=(
            'Use the ID of an ordinary QuickBooks customer. QuickBooks Projects/Jobs '
            'are intentionally not used.'
        ),
    )

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.fields['project'].queryset = organization.projects.all()
        self.fields['connection'].queryset = organization.quickbooks_connections.filter(
            status=QuickBooksConnection.Status.CONNECTED
        )

    def clean_quickbooks_customer_id(self):
        customer_id = self.cleaned_data['quickbooks_customer_id'].strip()
        if not customer_id:
            raise forms.ValidationError('Enter a QuickBooks customer ID.')
        return customer_id


class QuickBooksItemMappingForm(forms.Form):
    cost_code = forms.ModelChoiceField(queryset=CostCode.objects.none())
    connection = forms.ModelChoiceField(queryset=QuickBooksConnection.objects.none())
    quickbooks_item_id = forms.CharField(
        max_length=50,
        label='QuickBooks item ID',
        help_text='Use the ID of an existing QuickBooks Product/Service item.',
    )

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.fields['cost_code'].queryset = organization.cost_codes.filter(is_active=True)
        self.fields['connection'].queryset = organization.quickbooks_connections.filter(
            status=QuickBooksConnection.Status.CONNECTED
        )

    def clean_quickbooks_item_id(self):
        item_id = self.cleaned_data['quickbooks_item_id'].strip()
        if not item_id:
            raise forms.ValidationError('Enter a QuickBooks item ID.')
        return item_id
