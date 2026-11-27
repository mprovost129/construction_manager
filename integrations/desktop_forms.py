import ntpath
import re

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.views.decorators.debug import sensitive_variables

from users.mfa_views import PasswordForm


class ConnectionForm(PasswordForm):
    company_name = forms.CharField(label='Exact QuickBooks company name', max_length=255)
    file_path = forms.CharField(label='Company file location shown in QuickBooks', max_length=500,
        widget=forms.TextInput(attrs={'autocomplete': 'off'}),
        help_text='Press F2 in QuickBooks and copy the Location ending in .QBW. Only a fingerprint of this path is stored; do not upload the file.')
    connector_password = forms.CharField(label='New Web Connector password', min_length=16, max_length=64,
        strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text='Choose a unique password of 16–64 characters. Save it privately and enter the same password in Web Connector. Do not use your QuickBooks or Heritage login password.')
    confirm_connector_password = forms.CharField(label='Confirm Web Connector password', max_length=64,
        strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))
    prepared = forms.BooleanField(label='I am authorized to connect this file, have a current backup, and have confirmed its storage and sync settings with the computer operator.')
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, (forms.CheckboxInput, forms.HiddenInput)):
                field.widget.attrs['class'] = 'form-control'
        self.fields['password'].label = 'Your current Heritage 360 password'
        self.order_fields(['company_name', 'file_path', 'connector_password', 'confirm_connector_password', 'password', 'prepared', 'revision'])

    def clean_company_name(self):
        name = self.cleaned_data['company_name']
        if any(ord(char) < 32 for char in name):
            raise forms.ValidationError('Enter the company name shown in QuickBooks.')
        return name

    def clean_file_path(self):
        path = self.cleaned_data['file_path'].strip()
        drive, tail = ntpath.splitdrive(path)
        if (not drive or not tail.startswith(('\\', '/')) or not path.lower().endswith('.qbw')
                or re.search(r'[<>"|?*\x00-\x1f]', path) or '://' in path
                or path.startswith(('\\\\?\\', '\\\\.\\'))):
            raise forms.ValidationError('Enter an absolute Windows or network path ending in .QBW.')
        return path

    @sensitive_variables()
    def clean(self):
        data = super().clean()
        password = data.get('connector_password')
        if password:
            validate_password(password, self.user)
            if self.user.check_password(password):
                self.add_error('connector_password', 'Use a different password from your Heritage login.')
        if password != data.get('confirm_connector_password'):
            self.add_error('confirm_connector_password', 'The connection passwords do not match.')
        return data


class ConnectionActionForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class DisconnectForm(PasswordForm, ConnectionActionForm):
    confirm = forms.BooleanField(label='Disconnect the shared company connection for everyone and invalidate its Web Connector password.')
