from decimal import Decimal

from .models import ChangeOrder, Estimate, FinishSelection
from .money import money


def project_financial_summary(project, *, include_costs=False):
    """Compute the client price rollup for a project (and, optionally, its internal cost side).

    Mirrors the client interview's model: the contract amount stays fixed, and
    approved change-order price deltas add to a running total project cost.
    Selection overages/credits are reported as flags only, never merged into
    the totals, matching the existing manual-follow-up workflow.
    """
    from billing.models import Invoice

    change_orders = list(project.change_orders.exclude(status=ChangeOrder.Status.VOIDED))
    approved_change_order_total = money(
        sum(
            (co.price_delta for co in change_orders if co.status == ChangeOrder.Status.APPROVED),
            Decimal('0'),
        )
    )
    pending_change_order_total = money(
        sum(
            (co.price_delta for co in change_orders if co.status == ChangeOrder.Status.PENDING),
            Decimal('0'),
        )
    )

    contract_amount = project.contract_amount
    total_project_cost = (
        money(contract_amount + approved_change_order_total)
        if contract_amount is not None
        else None
    )

    selections = project.finish_selections.filter(
        status=FinishSelection.Status.SELECTED
    ).select_related('chosen_option')
    selection_overage_total = Decimal('0')
    selection_credit_total = Decimal('0')
    selection_credit_by_disposition = {}
    for selection in selections:
        variance = selection.selected_variance
        if not variance:
            continue
        if variance > 0:
            selection_overage_total += variance
        else:
            credit = -variance
            selection_credit_total += credit
            selection_credit_by_disposition[selection.credit_disposition] = (
                selection_credit_by_disposition.get(selection.credit_disposition, Decimal('0'))
                + credit
            )

    invoices = list(project.invoices.exclude(status=Invoice.Status.VOIDED))
    invoiced_total = money(sum((invoice.total_amount for invoice in invoices), Decimal('0')))
    paid_total = money(sum((invoice.amount_paid for invoice in invoices), Decimal('0')))
    balance_due = money(sum((invoice.balance_due for invoice in invoices), Decimal('0')))

    summary = {
        'contract_amount': contract_amount,
        'approved_change_order_total': approved_change_order_total,
        'pending_change_order_total': pending_change_order_total,
        'total_project_cost': total_project_cost,
        'selection_overage_total': money(selection_overage_total),
        'selection_credit_total': money(selection_credit_total),
        'selection_credit_by_disposition': selection_credit_by_disposition,
        'invoiced_total': invoiced_total,
        'paid_total': paid_total,
        'balance_due': balance_due,
    }

    if include_costs:
        approved_change_order_cost_total = money(
            sum(
                (co.cost_delta for co in change_orders if co.status == ChangeOrder.Status.APPROVED),
                Decimal('0'),
            )
        )
        approved_estimate = project.estimates.filter(status=Estimate.Status.APPROVED).first()
        estimate_cost_total = approved_estimate.cost_total if approved_estimate else None
        estimated_margin = None
        if total_project_cost is not None and estimate_cost_total is not None:
            estimated_margin = money(
                total_project_cost - estimate_cost_total - approved_change_order_cost_total
            )
        summary.update(
            {
                'approved_change_order_cost_total': approved_change_order_cost_total,
                'estimate_cost_total': estimate_cost_total,
                'estimated_margin': estimated_margin,
            }
        )

    return summary
