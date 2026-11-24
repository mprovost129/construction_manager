"""Customer allowances. Shared budgets are deducted once, never once per row."""

from collections import defaultdict
from decimal import Decimal

from .models import FinishSelection

ZERO = Decimal("0")


def recorded_price(row):
    if hasattr(row, "board_price"):
        return row.board_price
    if row.status == FinishSelection.Status.IMPORTED:
        return row.imported_total
    if row.status == FinishSelection.Status.SELECTED and row.chosen_option_id:
        return row.chosen_option.price
    return None


def allowance_totals(rows, *, complete_pool_ids=None):
    active = [r for r in rows if r.status != FinishSelection.Status.VOIDED]
    priced = [r for r in active if recorded_price(r) is not None]
    individual = [r for r in active if getattr(r, "has_individual_allowance", True)]
    own_priced = [r for r in individual if recorded_price(r) is not None]
    own_pending = [r for r in individual if recorded_price(r) is None]
    allowance = sum((r.allowance_amount for r in own_priced), ZERO)
    pending_allowance = sum((r.allowance_amount for r in own_pending), ZERO)
    spending = sum((recorded_price(r) for r in own_priced), ZERO)
    pools = defaultdict(list)
    for row in active:
        if getattr(row, "uses_section_allowance", False) and row.package_id:
            pools[row.package_id].append(row)
    shared = []
    partial_pool = False
    for pk, members in pools.items():
        package = members[0].package
        if package.allowance_amount is None:
            continue
        if complete_pool_ids is not None and {
            r.pk for r in members
        } != complete_pool_ids.get(pk, set()):
            partial_pool = True
            continue
        cost = sum(
            (recorded_price(r) for r in members if recorded_price(r) is not None), ZERO
        )
        pending = sum(recorded_price(r) is None for r in members)
        shared.append(
            dict(
                package=package,
                allowance=package.allowance_amount,
                price=cost,
                remaining=package.allowance_amount - cost,
                pending_count=pending,
            )
        )
        allowance += package.allowance_amount
        spending += cost
    return dict(
        price=sum((recorded_price(r) for r in priced), ZERO),
        included_price=sum((recorded_price(r) for r in priced if getattr(r, 'allowance_mode', 'individual') == 'none'), ZERO),
        priced_allowance=allowance,
        pending_allowance=pending_allowance,
        allowance=allowance + pending_allowance,
        remaining=allowance - spending,
        priced_count=len(priced),
        pending_count=len(active) - len(priced),
        excluded_count=len(rows) - len(active),
        shared=shared,
        partial_pool=partial_pool,
        has_allowance=bool(individual or shared),
        comparison_count=len(own_priced) + len(shared),
    )


def pool_members(rows):
    result = defaultdict(set)
    for row in rows:
        if row.uses_section_allowance and row.status != FinishSelection.Status.VOIDED:
            result[row.package_id].add(row.pk)
    return result


def financial_links(row):
    """Changing an already billed basis would silently reinterpret a ledger entry."""
    return (
        hasattr(row, "invoice")
        or row.credit_change_orders.exists()
        or row.credit_disposition != FinishSelection.CreditDisposition.UNDETERMINED
    )
