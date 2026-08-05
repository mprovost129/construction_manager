from datetime import timedelta
from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import Invoice, InvoiceLineItem, Payment


class InvoiceDraftForm(forms.ModelForm):
    tax_rate = forms.DecimalField(
        required=False,
        min_value=0,
        max_value=100,
        label='Tax rate (%)',
        help_text='Tax is computed automatically from this rate. Set to 0 for tax-exempt.',
        widget=forms.NumberInput(attrs={'step': '0.001', 'min': '0', 'max': '100'}),
    )

    class Meta:
        model = Invoice
        fields = ('title', 'due_date', 'tax_rate', 'notes')
        widgets = {
            'due_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        if not self.is_bound and not self.instance.pk:
            self.initial.setdefault('due_date', timezone.localdate() + timedelta(days=30))
            self.initial.setdefault('tax_rate', organization.default_tax_rate)

    def clean_due_date(self):
        due_date = self.cleaned_data['due_date']
        if not due_date:
            raise forms.ValidationError('Enter the invoice due date.')
        return due_date

    def clean_tax_rate(self):
        tax_rate = self.cleaned_data.get('tax_rate')
        return tax_rate if tax_rate is not None else self.organization.default_tax_rate


class InvoiceLineItemForm(forms.ModelForm):
    class Meta:
        model = InvoiceLineItem
        fields = (
            'category',
            'cost_code',
            'description',
            'quantity',
            'unit_price',
            'sort_order',
        )
        labels = {'cost_code': 'Cost code'}
        help_texts = {'cost_code': 'Optional job-costing code.'}
        widgets = {
            'quantity': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
            'unit_price': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        }

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cost_code'].queryset = organization.cost_codes.filter(is_active=True)


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ('amount', 'method', 'reference', 'paid_date', 'note')
        widgets = {
            'amount': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
            'paid_date': forms.DateInput(attrs={'type': 'date'}),
            'note': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial.setdefault('paid_date', timezone.localdate())


class InvoiceVoidForm(forms.Form):
    reason = forms.CharField(
        max_length=255,
        widget=forms.Textarea(attrs={'rows': 3}),
    )

    def clean_reason(self):
        reason = self.cleaned_data['reason'].strip()
        if not reason:
            raise forms.ValidationError('Explain why this invoice is being voided.')
        return reason


class CreditMemoApplyForm(forms.Form):
    invoice = forms.ModelChoiceField(queryset=Invoice.objects.none())
    amount = forms.DecimalField(
        min_value=Decimal('0.01'),
        max_digits=14,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
    )

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['invoice'].queryset = project.invoices.filter(
            status__in=(Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID)
        )


class CreditMemoVoidForm(forms.Form):
    reason = forms.CharField(
        max_length=255,
        widget=forms.Textarea(attrs={'rows': 3}),
    )

    def clean_reason(self):
        reason = self.cleaned_data['reason'].strip()
        if not reason:
            raise forms.ValidationError('Explain why this credit memo is being voided.')
        return reason

