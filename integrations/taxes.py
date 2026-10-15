"""Explicit mappings for the app's flat-rate invoices to US manual sales tax codes."""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from projects.access import can_manage_organization
from projects.models import ActivityEvent
from projects.services import record_activity

from .models import QuickBooksConnection, QuickBooksTaxCodeMapping
from .quickbooks import QuickBooksAccountingClient
from .services import QuickBooksMappingError

MISSING_TAX_MAPPING = (
    "Taxed invoices cannot be synchronized without a verified sales tax mapping. "
    "Choose the matching rate and jurisdiction on the QuickBooks sales tax page."
)


def require_supported_tax_company(connection, api):
    if connection.status != QuickBooksConnection.Status.CONNECTED:
        raise QuickBooksMappingError(
            "Reconnect QuickBooks before configuring sales tax."
        )
    if connection.country != "US":
        raise QuickBooksMappingError(
            "Sales tax mapping currently supports US QuickBooks companies only."
        )
    preferences = api.get_preferences(connection)
    tax = preferences.get("TaxPrefs") or {}
    if tax.get("UsingSalesTax") is not True:
        raise QuickBooksMappingError(
            "Enable sales tax in QuickBooks before mapping a rate."
        )
    if tax.get("PartnerTaxEnabled"):
        raise QuickBooksMappingError(
            "Automated QuickBooks sales tax is not supported by this flat-rate integration yet."
        )
    if (preferences.get("CurrencyPrefs") or {}).get("MultiCurrencyEnabled"):
        raise QuickBooksMappingError(
            "Taxed invoice export currently requires a single-currency company."
        )


def read_tax_code(connection, code_id, api, *, code=None):
    code = code if code is not None else api.get_tax_code(connection, code_id)
    if (
        str(code.get("Id")) != str(code_id)
        or code.get("Active") is not True
        or code.get("Taxable") is not True
        or code.get("TaxGroup") is not True
        or code.get("Hidden") is True
        or str(code_id) in {"TAX", "NON", "CustomSalesTax"}
    ):
        raise QuickBooksMappingError(
            "Choose an active QuickBooks sales tax code with defined rates."
        )
    details = (code.get("SalesTaxRateList") or {}).get("TaxRateDetail")
    if not isinstance(details, list) or not details:
        raise QuickBooksMappingError(
            "This QuickBooks tax code has no supported sales rates."
        )
    total = Decimal("0")
    seen = set()
    rates = []
    for detail in details:
        if (
            not isinstance(detail, dict)
            or detail.get("TaxTypeApplicable") != "TaxOnAmount"
        ):
            raise QuickBooksMappingError(
                "Compound tax codes are not supported by the flat-rate invoice calculation."
            )
        rate_id = str((detail.get("TaxRateRef") or {}).get("value") or "")
        if not rate_id or rate_id in seen:
            raise QuickBooksMappingError(
                "QuickBooks returned incomplete sales tax rates."
            )
        seen.add(rate_id)
        rate = api.get_tax_rate(connection, rate_id)
        if (
            str(rate.get("Id")) != rate_id
            or rate.get("Active") is not True
            or rate.get("EffectiveTaxRate")
            or rate.get("EffectiveTaxRateList")
        ):
            raise QuickBooksMappingError(
                "Inactive or date-dependent tax rates require review before export."
            )
        try:
            value = Decimal(str(rate["RateValue"]))
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise QuickBooksMappingError(
                "QuickBooks returned an invalid tax percentage."
            ) from exc
        if not value.is_finite() or value < 0 or value > 100:
            raise QuickBooksMappingError(
                "QuickBooks returned an invalid tax percentage."
            )
        total += value
        rates.append({"id": rate_id, "rate": str(value)})
    if total <= 0 or total > 100 or total != total.quantize(Decimal(".001")):
        raise QuickBooksMappingError(
            "This tax percentage is not supported by the invoice calculation."
        )
    return {
        "id": str(code_id),
        "name": str(code.get("Name") or code_id),
        "rate": total,
        "rates": rates,
    }


def tax_code_options(connection, *, api_client=None):
    api = api_client or QuickBooksAccountingClient()
    require_supported_tax_company(connection, api)
    options = []
    for code in api.iter_tax_codes(connection):
        try:
            options.append(read_tax_code(connection, code.get("Id"), api, code=code))
        except QuickBooksMappingError:
            continue
    return options


def save_tax_mapping(*, connection, local_tax_rate, code_id, actor, api_client=None):
    if not can_manage_organization(actor, connection.organization):
        raise PermissionDenied
    api = api_client or QuickBooksAccountingClient()
    require_supported_tax_company(connection, api)
    values = read_tax_code(connection, code_id, api)
    if values["rate"] != local_tax_rate:
        raise QuickBooksMappingError(
            "The local percentage must exactly match the combined QuickBooks sales tax rate."
        )
    with transaction.atomic():
        # Serialize changes to the rate map for this connection.
        QuickBooksConnection.objects.select_for_update().get(pk=connection.pk)
        previous = QuickBooksTaxCodeMapping.objects.filter(
            connection=connection, local_tax_rate=local_tax_rate
        ).first()
        old_code = previous.quickbooks_tax_code_id if previous else None
        snapshot = {**values, "rate": str(values["rate"])}
        mapping, _ = QuickBooksTaxCodeMapping.objects.update_or_create(
            connection=connection,
            local_tax_rate=local_tax_rate,
            defaults={
                "quickbooks_tax_code_id": values["id"],
                "quickbooks_tax_code_name": values["name"],
                "is_active": True,
                "verified_at": timezone.now(),
                "last_verified_values": snapshot,
            },
        )
        record_activity(
            organization=connection.organization,
            actor=actor,
            event_type=ActivityEvent.Type.QUICKBOOKS_TAX_MAPPED,
            summary=f"Sales tax {local_tax_rate}% mapped to {values['name']}.",
            metadata={
                "connection_id": connection.pk,
                "rate": str(local_tax_rate),
                "previous_tax_code_id": old_code,
                "tax_code_id": values["id"],
            },
        )
    return mapping


@transaction.atomic
def unlink_tax_mapping(mapping, *, actor):
    mapping = (
        QuickBooksTaxCodeMapping.objects.select_for_update()
        .select_related("connection__organization")
        .get(pk=mapping.pk)
    )
    if not can_manage_organization(actor, mapping.connection.organization):
        raise PermissionDenied
    if not mapping.is_active:
        return
    mapping.is_active = False
    mapping.save(update_fields=("is_active", "updated_at"))
    record_activity(
        organization=mapping.connection.organization,
        actor=actor,
        event_type=ActivityEvent.Type.QUICKBOOKS_TAX_UNLINKED,
        summary=f"Sales tax mapping for {mapping.local_tax_rate}% removed.",
        metadata={
            "mapping_id": mapping.pk,
            "tax_code_id": mapping.quickbooks_tax_code_id,
        },
    )


def invoice_tax_mapping(invoice, connection):
    mapping = QuickBooksTaxCodeMapping.objects.filter(
        connection=connection, local_tax_rate=invoice.tax_rate, is_active=True
    ).first()
    if not mapping:
        raise QuickBooksMappingError(MISSING_TAX_MAPPING)
    return mapping


def validate_invoice_tax(invoice, connection, payload, api):
    """Check current rates before writing without changing a persisted retry payload."""
    if not invoice.tax_rate:
        return
    mapping = invoice_tax_mapping(invoice, connection)
    require_supported_tax_company(connection, api)
    code_id = (payload.get("TxnTaxDetail", {}).get("TxnTaxCodeRef") or {}).get("value")
    if code_id != mapping.quickbooks_tax_code_id or any(
        (line.get("SalesItemLineDetail", {}).get("TaxCodeRef") or {}).get("value")
        != "TAX"
        for line in payload.get("Line", [])
    ):
        raise QuickBooksMappingError(
            "The queued invoice uses a different or missing tax mapping. Review it before retrying."
        )
    values = read_tax_code(connection, code_id, api)
    if values["rate"] != invoice.tax_rate:
        raise QuickBooksMappingError(
            "The QuickBooks tax rate changed. Review the mapping and issued invoice before exporting."
        )
