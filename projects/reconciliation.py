"""Read-only customer-price reconciliation, independent of invoices and payments."""

import re
from collections import defaultdict
from decimal import Decimal

from .models import ChangeOrder, FinishSelection
from .money import money

ZERO = Decimal("0")


def category_key(value):
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
    )


def project_reconciliation(project, *, client_view=True):
    order_query = project.change_orders.all()
    if client_view:
        order_query = order_query.exclude(status=ChangeOrder.Status.DRAFT)
    orders = list(order_query.order_by("number", "pk").prefetch_related("line_items"))
    approved, pending = [], []
    linked = defaultdict(lambda: ZERO)
    linked_orders = defaultdict(list)
    review_warnings = []
    reviewed_selection_ids = set()
    approved_total, pending_total = ZERO, ZERO
    excluded_orders = 0
    for order in orders:
        if order.status in (ChangeOrder.Status.DECLINED, ChangeOrder.Status.VOIDED):
            excluded_orders += 1
            continue
        lines = [
            {
                "description": line.description,
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "price": money(line.total_price),
            }
            for line in order.line_items.all()
        ]
        # Stored order totals are authoritative. Older imports may only have a total,
        # or a total that includes adjustments absent from their exported line items.
        difference = money(
            order.price_delta - sum((line["price"] for line in lines), ZERO)
        )
        if not lines or difference:
            lines.append(
                {
                    "description": "Order total (no itemized detail)"
                    if not lines
                    else "Adjustment to recorded order total",
                    "quantity": None,
                    "unit_price": None,
                    "price": order.price_delta if not lines else difference,
                }
            )
        item = {"order": order, "lines": lines, "total": order.price_delta}
        if order.status in (ChangeOrder.Status.APPROVED, ChangeOrder.Status.IMPORTED):
            approved.append(item)
            approved_total += order.price_delta
            if order.source_selection_id:
                linked[order.source_selection_id] += order.price_delta
                linked_orders[order.source_selection_id].append(order)
        else:
            pending.append(item)
            pending_total += order.price_delta

    groups = {}
    unpriced_count, unpriced_allowance = 0, ZERO
    selection_query = project.finish_selections.exclude(
        status=FinishSelection.Status.VOIDED
    )
    if client_view:
        selection_query = selection_query.exclude(status=FinishSelection.Status.DRAFT)
    for selection in selection_query.select_related("package", "chosen_option"):
        if selection.status == FinishSelection.Status.IMPORTED:
            price = selection.imported_total
        elif (
            selection.status == FinishSelection.Status.SELECTED
            and selection.chosen_option_id
        ):
            price = selection.chosen_option.price
        else:
            price = None
        if price is None:
            unpriced_count += 1
            unpriced_allowance += selection.allowance_amount
            continue
        label = selection.package.title if selection.package_id else "Uncategorized"
        group = groups.setdefault(
            label,
            {
                "label": label,
                "price": ZERO,
                "allowance": ZERO,
                "variance": ZERO,
                "in_change_orders": ZERO,
                "net": ZERO,
                "count": 0,
                "has_amount": False,
                "details": [],
            },
        )
        variance = price - selection.allowance_amount
        covered = linked.get(selection.pk, ZERO)
        sources = linked_orders.get(selection.pk, [])
        reviewed_selection_ids.add(selection.pk)
        if sources and (
            len(sources) > 1
            or not (variance < 0 and covered < 0 and abs(covered) <= abs(variance))
        ):
            review_warnings.append(
                f"{selection.display_number}: linked change-order credit needs review against the current selection amount."
            )
        group["details"].append(
            {
                "selection": selection,
                "price": price,
                "allowance": selection.allowance_amount,
                "variance": variance,
                "covered": covered,
                "net": variance - covered,
                "orders": sources,
            }
        )
        # A linked partial credit consumes only that amount, not the entire credit.
        group["price"] += price
        group["allowance"] += selection.allowance_amount
        group["variance"] += variance
        group["in_change_orders"] += covered
        group["net"] += variance - covered
        group["count"] += 1
        group["has_amount"] |= bool(price or selection.allowance_amount or covered)
    sections = [
        groups[label]
        for label in sorted(groups, key=category_key)
        if groups[label]["has_amount"]
    ]
    for selection_id, sources in linked_orders.items():
        if selection_id not in reviewed_selection_ids:
            review_warnings.append(
                f"{sources[0].display_number}: linked selection is not included in this report. Ask the project manager to review the recorded credit."
            )
    selection_total = money(sum((section["net"] for section in sections), ZERO))
    adjustment = money(approved_total + selection_total)
    return {
        "review_warnings": review_warnings,
        "approved_orders": approved,
        "pending_orders": pending,
        "sections": sections,
        "approved_total": money(approved_total),
        "pending_total": money(pending_total),
        "selection_total": selection_total,
        "adjustment": adjustment,
        "original_price": project.contract_amount,
        "new_total": money(project.contract_amount + adjustment)
        if project.contract_amount is not None
        else None,
        "selection_price": money(sum((section["price"] for section in sections), ZERO)),
        "selection_allowance": money(
            sum((section["allowance"] for section in sections), ZERO)
        ),
        "linked_total": money(
            sum((section["in_change_orders"] for section in sections), ZERO)
        ),
        "unpriced_count": unpriced_count,
        "unpriced_allowance": money(unpriced_allowance),
        "excluded_orders": excluded_orders,
    }
