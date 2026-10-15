"""Explicit sales-account selection for newly created Service items."""

from .services import QuickBooksMappingError

SUPPORTED_INCOME_SUBTYPES = {"ServiceFeeIncome", "SalesOfProductIncome"}


def validate_income_account(account, account_id):
    if (
        not isinstance(account, dict)
        or not account_id
        or str(account.get("Id")) != str(account_id)
        or account.get("Active") is not True
        or account.get("AccountType") != "Income"
        or account.get("AccountSubType") not in SUPPORTED_INCOME_SUBTYPES
    ):
        raise QuickBooksMappingError(
            "Choose an active Service/Fee Income or Sales of Product Income account from this QuickBooks company."
        )
    return {
        "value": str(account["Id"]),
        "name": str(
            account.get("FullyQualifiedName") or account.get("Name") or account_id
        ),
    }


def income_account_options(connection, api):
    options = []
    for account in api.iter_income_accounts(connection):
        try:
            options.append(validate_income_account(account, account.get("Id")))
        except QuickBooksMappingError:
            continue
    return options
