"""Read invoice applications from QBO Payment lines, including applied credits.

QBO documents credit applications as Payment records with invoice and CreditMemo
links. TotalAmt is cash received, not the amount applied to any one invoice:
https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/payment
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from projects.money import money

from .quickbooks import QuickBooksAPIError


def _invalid_payment():
    return QuickBooksAPIError(
        'invalid_payment_allocation',
        'QuickBooks returned incomplete or ambiguous payment applications. Review the payment in QuickBooks.',
    )


def _amount(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise _invalid_payment()
        if result != money(result):
            raise _invalid_payment()
        return result
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise _invalid_payment() from exc


def invoice_payment_application(payment, invoice_mapping):
    """Return this invoice's applied amount, cash total, and transaction date."""
    if (
        not isinstance(payment, dict)
        or not payment.get('Id')
        or payment.get('SyncToken') is None
    ):
        raise _invalid_payment()
    customer = payment.get('CustomerRef')
    if (
        not isinstance(customer, dict)
        or str(customer.get('value')) != invoice_mapping.quickbooks_customer_id
    ):
        raise _invalid_payment()
    total = _amount(payment.get('TotalAmt'))
    try:
        paid_date = date.fromisoformat(payment['TxnDate'])
    except (KeyError, TypeError, ValueError) as exc:
        raise _invalid_payment() from exc
    lines = payment.get('Line')
    if not isinstance(lines, list):
        raise _invalid_payment()
    applied = Decimal('0.00')
    for line in lines:
        if not isinstance(line, dict) or not isinstance(line.get('LinkedTxn'), list):
            raise _invalid_payment()
        links = line['LinkedTxn']
        if not all(
            isinstance(link, dict) and link.get('TxnId') and link.get('TxnType')
            for link in links
        ):
            raise _invalid_payment()
        invoice_ids = {
            str(link['TxnId']) for link in links if link['TxnType'] == 'Invoice'
        }
        if invoice_mapping.quickbooks_invoice_id not in invoice_ids:
            continue
        # One amount cannot safely be divided between multiple invoice references.
        if len(invoice_ids) != 1:
            raise _invalid_payment()
        applied += _amount(line.get('Amount'))
    return money(applied), total, paid_date
