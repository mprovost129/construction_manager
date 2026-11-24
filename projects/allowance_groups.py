"""Validated named budgets within a selection category; amounts stay decimal strings."""

from decimal import Decimal
from uuid import UUID

from django import forms
from django.core.exceptions import ValidationError


def validate_groups(value):
    if not isinstance(value, list) or len(value) > 50:
        raise ValidationError("Use at most 50 allowance groups per section.")
    ids, names = set(), set()
    amount_field = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2)
    cleaned = []
    for group in value:
        try:
            key = str(UUID(group["id"]))
            name = group["name"].strip()
            amount = amount_field.clean(group["amount"])
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValidationError(
                "Each allowance group needs a name and a valid amount."
            )
        if not name or len(name) > 100 or key in ids or name.casefold() in names:
            raise ValidationError(
                "Give each allowance group a unique name (up to 100 characters)."
            )
        ids.add(key)
        names.add(name.casefold())
        cleaned.append(dict(id=key, name=name, amount=str(amount)))
    if sum((Decimal(g["amount"]) for g in cleaned), Decimal("0")) > Decimal(
        "9999999999.99"
    ):
        raise ValidationError("The combined allowance exceeds the supported amount.")
    return cleaned


def pool_key(row):
    return (row.package_id, row.allowance_group)


def pool_budget(row):
    if not row.package_id:
        return None
    if row.package.allowance_groups:
        return next(
            (
                Decimal(g["amount"])
                for g in row.package.allowance_groups
                if g["id"] == row.allowance_group
            ),
            None,
        )
    return row.package.allowance_amount if not row.allowance_group else None


def pool_label(row):
    if row.package_id and row.allowance_group:
        return next(
            (
                g["name"]
                for g in row.package.allowance_groups
                if g["id"] == row.allowance_group
            ),
            "Unavailable allowance group",
        )
    return "Shared section allowance"


def copied_budget(section, group_ids):
    """A partial copy brings only the named budgets used by the copied rows."""
    if not section:
        return dict(allowance_amount=None, allowance_groups=[])
    if not section.allowance_groups:
        return dict(allowance_amount=section.allowance_amount, allowance_groups=[])
    groups = [g.copy() for g in section.allowance_groups if g["id"] in group_ids]
    return dict(
        allowance_groups=groups,
        allowance_amount=(
            sum((Decimal(g["amount"]) for g in groups), Decimal("0"))
            if groups
            else None
        ),
    )
