from decimal import Decimal

from django import forms

from projects.models import CostCode, Project

from .models import QuickBooksConnection


class QuickBooksServiceItemForm(forms.Form):
    income_account = forms.ChoiceField(label='Income account')

    def __init__(self, *args, options, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['income_account'].choices = [('', 'Choose an income account')] + [
            (option['value'], option['name']) for option in options
        ]


class QuickBooksTaxMappingForm(forms.Form):
    local_tax_rate = forms.DecimalField(
        label='Invoice tax percentage', max_digits=6, decimal_places=3,
        min_value=Decimal('.001'), max_value=Decimal('100'),
    )
    tax_code = forms.ChoiceField(label='QuickBooks sales tax code')

    def __init__(self, *args, options, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tax_code'].choices = [('', 'Choose a rate and jurisdiction')] + [
            (option['id'], f'{option["name"]} — {option["rate"]}%') for option in options
        ]


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
