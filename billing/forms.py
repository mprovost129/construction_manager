from datetime import timedelta

from django import forms
from django.utils import timezone

from .models import Invoice, InvoiceLineItem


class InvoiceDraftForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ('title', 'due_date', 'tax_amount', 'notes')
        widgets = {
            'due_date': forms.DateInput(attrs={'type': 'date'}),
            'tax_amount': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'notes': forms.Textarea(attrs={'rows': 4}),
        }
        labels = {'tax_amount': 'Tax'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.instance.pk:
            self.initial.setdefault('due_date', timezone.localdate() + timedelta(days=30))

    def clean_due_date(self):
        due_date = self.cleaned_data['due_date']
        if not due_date:
            raise forms.ValidationError('Enter the invoice due date.')
        return due_date


class InvoiceLineItemForm(forms.ModelForm):
    class Meta:
        model = InvoiceLineItem
        fields = ('category', 'description', 'quantity', 'unit_price', 'sort_order')
        widgets = {
            'quantity': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
            'unit_price': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        }


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

