from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_http_methods

from projects.access import accounting_organizations_for_user
from users.mfa_views import password_correct

from .desktop_forms import ConnectionActionForm, ConnectionForm, DisconnectForm
from .desktop_setup import allowed, change_connection, configure, current_connection, qwc_document

ERROR_HELP = {
    'COMPANY_FILE_MISMATCH': 'The open file location does not match this setup. Open the intended file. If IT moved it, contact the site owner before resetting the connection.',
    'UNSUPPORTED_XML_VERSION': 'This connection requires QuickBooks with qbXML 13 or newer. Confirm the installed QuickBooks and Web Connector versions.',
    'COMPANY_RESPONSE_REJECTED': 'The company name or connection identifier did not match. Confirm the exact company name and that this setup file was added with the intended company open.',
    'QUICKBOOKS_UNAVAILABLE': 'Web Connector could not open QuickBooks. Open the intended company and close any QuickBooks dialog boxes, then request another check.',
    'RESPONSE_OUT_OF_SEQUENCE': 'QuickBooks could not finish the check. Request another check and run Update Selected once.',
    'CONFLICTING_RESPONSE': 'Conflicting responses were received. Keep only one connection computer running this setup and request another check.',
}


@login_required
@require_http_methods(['GET', 'POST'])
@sensitive_post_parameters()
@sensitive_variables()
def connection_page(request, slug):
    organization = get_object_or_404(accounting_organizations_for_user(request.user), slug=slug)
    connection = current_connection(organization)
    revision = connection.configuration_revision if connection else 0
    can_manage = allowed(request.user, organization, 'manage')
    can_check = allowed(request.user, organization, 'check')
    form = ConnectionForm(user=request.user, initial={'revision': revision,
        'company_name': connection.expected_company_name if connection else ''})
    disconnect_form = DisconnectForm(initial={'revision': revision})
    check_form = ConnectionActionForm(initial={'revision': revision})
    if request.method == 'POST':
        if not settings.QUICKBOOKS_DESKTOP_CONNECTOR_ENABLED:
            raise PermissionDenied('Connection checks are temporarily disabled by the site owner.')
        action = request.POST.get('action')
        if action not in ('configure', 'disconnect', 'check'):
            raise PermissionDenied('Unsupported connection action.')
        if not (can_check if action == 'check' else can_manage):
            raise PermissionDenied('Your administrator has not granted this connection permission.')
        if action == 'configure':
            form = ConnectionForm(request.POST, user=request.user)
            active_form = form
        elif action == 'disconnect':
            disconnect_form = DisconnectForm(request.POST)
            active_form = disconnect_form
        else:
            check_form = ConnectionActionForm(request.POST)
            active_form = check_form
        if active_form.is_valid() and (action == 'check' or password_correct(request, active_form)):
            try:
                if action == 'configure':
                    configure(organization=organization, actor=request.user,
                        company_name=form.cleaned_data['company_name'], file_path=form.cleaned_data['file_path'],
                        password=form.cleaned_data['connector_password'], revision=form.cleaned_data['revision'])
                    messages.success(request, 'Setup prepared. Download the setup file below and add it in Web Connector. No QuickBooks connection has been verified yet.')
                else:
                    change_connection(organization=organization, actor=request.user,
                                      action=action, revision=active_form.cleaned_data['revision'])
                    messages.success(request, 'Connection disconnected for everyone.' if action == 'disconnect'
                        else 'Check queued. On the QuickBooks computer, open the intended company and click Update Selected in Web Connector.')
                return redirect('integrations:desktop_connection', slug=slug)
            except ValidationError as error:
                active_form.add_error(None, error)
    return render(request, 'integrations/desktop_connection.html', {
        'organization': organization, 'connection': connection, 'form': form,
        'disconnect_form': disconnect_form, 'check_form': check_form,
        'can_manage_connection': can_manage, 'can_check_connection': can_check,
        'connector_enabled': settings.QUICKBOOKS_DESKTOP_CONNECTOR_ENABLED,
        'connection_error': ERROR_HELP.get(connection.last_error_code, 'Request another check or contact the site owner.') if connection and connection.last_error_code else '',
    })


@login_required
@require_GET
def download_qwc(request, slug):
    organization = get_object_or_404(accounting_organizations_for_user(request.user), slug=slug)
    if not allowed(request.user, organization, 'manage'):
        raise PermissionDenied
    if not settings.QUICKBOOKS_DESKTOP_CONNECTOR_ENABLED:
        raise Http404
    connection = current_connection(organization)
    if not connection or not connection.active:
        raise Http404
    response = HttpResponse(qwc_document(connection), content_type='application/x-qwc')
    response['Content-Disposition'] = 'attachment; filename="Heritage360-QuickBooks.qwc"'
    response['Cache-Control'] = 'no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@require_GET
def support(request):
    # QBWC checks this public URL during installation. No account, company details,
    # session cookies or database reads are required to load the instructions.
    return render(request, 'integrations/desktop_support.html')
