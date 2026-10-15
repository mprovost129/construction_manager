from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from projects.access import can_manage_organization
from projects.models import CostCode

from .customer_sync import QuickBooksSyncError
from .forms import QuickBooksServiceItemForm
from .item_accounts import income_account_options
from .item_sync import start_cost_code_item_sync
from .models import QuickBooksConnection
from .quickbooks import QuickBooksAccountingClient, QuickBooksAPIError
from .services import QuickBooksMappingError


@login_required
@require_http_methods(["GET", "POST"])
def service_item_setup(request, cost_code_id):
    cost_code = get_object_or_404(
        CostCode.objects.select_related("organization"), pk=cost_code_id
    )
    if not can_manage_organization(request.user, cost_code.organization):
        raise PermissionDenied(
            "Only company administrators can configure QuickBooks items."
        )
    data = request.POST if request.method == "POST" else request.GET
    connection_id = data.get("connection", "")
    connection = get_object_or_404(
        QuickBooksConnection,
        pk=connection_id if connection_id.isdecimal() else None,
        organization=cost_code.organization,
        status="connected",
    )
    api = QuickBooksAccountingClient()
    options, error = [], ""
    try:
        options = income_account_options(connection, api)
    except (QuickBooksAPIError, QuickBooksMappingError) as exc:
        error = getattr(exc, "public_message", str(exc))
    form = QuickBooksServiceItemForm(
        request.POST if request.method == "POST" else None, options=options
    )
    if request.method == "POST" and not error and form.is_valid():
        try:
            attempt = start_cost_code_item_sync(
                cost_code_id=cost_code.pk,
                connection_id=connection.pk,
                actor=request.user,
                income_account_id=form.cleaned_data["income_account"],
                api_client=api,
            )
        except (QuickBooksAPIError, QuickBooksMappingError, QuickBooksSyncError) as exc:
            form.add_error(None, getattr(exc, "public_message", str(exc)))
        else:
            if attempt.status == "succeeded":
                messages.success(
                    request, "QuickBooks service item verified and mapped."
                )
                return redirect(
                    reverse("integrations:quickbooks_connect")
                    + "?"
                    + urlencode({"organization": cost_code.organization.slug})
                )
            form.add_error(None, attempt.error_message)
    return render(
        request,
        "integrations/quickbooks_service_item.html",
        {
            "cost_code": cost_code,
            "connection": connection,
            "form": form,
            "item_name": (cost_code.name or cost_code.code)[:100],
            "error": error,
            "options": options,
        },
    )
