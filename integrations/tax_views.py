from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from projects.access import can_manage_organization

from .forms import QuickBooksTaxMappingForm
from .models import QuickBooksConnection, QuickBooksTaxCodeMapping
from .quickbooks import QuickBooksAPIError
from .services import QuickBooksMappingError
from .taxes import save_tax_mapping, tax_code_options, unlink_tax_mapping


@login_required
@require_http_methods(["GET", "POST"])
def tax_mappings(request, connection_id):
    connection = get_object_or_404(
        QuickBooksConnection.objects.select_related("organization"), pk=connection_id
    )
    if not can_manage_organization(request.user, connection.organization):
        raise PermissionDenied
    options = []
    error = ""
    try:
        options = tax_code_options(connection)
    except (QuickBooksAPIError, QuickBooksMappingError) as exc:
        error = getattr(exc, "public_message", str(exc))
    form = QuickBooksTaxMappingForm(
        request.POST if request.method == "POST" else None, options=options
    )
    if request.method == "POST" and not error and form.is_valid():
        try:
            save_tax_mapping(
                connection=connection,
                local_tax_rate=form.cleaned_data["local_tax_rate"],
                code_id=form.cleaned_data["tax_code"],
                actor=request.user,
            )
        except (QuickBooksAPIError, QuickBooksMappingError) as exc:
            form.add_error(None, getattr(exc, "public_message", str(exc)))
        else:
            messages.success(request, "Sales tax mapping verified and saved.")
            return redirect(
                "integrations:quickbooks_tax_mappings", connection_id=connection.pk
            )
    return render(
        request,
        "integrations/quickbooks_taxes.html",
        {
            "connection": connection,
            "form": form,
            "error": error,
            "options": options,
            "mappings": connection.tax_code_mappings.filter(is_active=True),
        },
    )


@login_required
@require_POST
def tax_mapping_unlink(request, mapping_id):
    mapping = get_object_or_404(
        QuickBooksTaxCodeMapping.objects.select_related("connection__organization"),
        pk=mapping_id,
    )
    unlink_tax_mapping(mapping, actor=request.user)
    messages.success(
        request, "Sales tax mapping removed. Existing invoices were not changed."
    )
    return redirect(
        "integrations:quickbooks_tax_mappings", connection_id=mapping.connection_id
    )
