"""Staff-only, audited allowance setup for a project or a reusable template."""

import hashlib
import json
from collections import OrderedDict

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .allowances import financial_links
from .models import (
    ActivityEvent,
    AllowanceMode,
    Project,
    SelectionTemplate,
    SelectionTemplateSection,
)
from .reconciliation import category_key
from .services import record_activity


def setup_state(owner, template=False, lock=False):
    rows_qs = owner.rows.all() if template else owner.finish_selections.all()
    if lock:
        rows_qs = rows_qs.select_for_update()
    rows = list(rows_qs.order_by("pk"))
    packages = list(
        owner.allowance_sections.all() if template else owner.selection_packages.all()
    )
    groups = OrderedDict()
    if template:
        budgets = {p.category.casefold(): p.allowance_amount for p in packages}
        for row in sorted(
            rows, key=lambda r: (category_key(r.category), r.sort_order, r.pk)
        ):
            key = row.category.strip().casefold()
            groups.setdefault(
                key,
                dict(
                    label=row.category or "Uncategorized",
                    category=row.category,
                    amount=budgets.get(key),
                    rows=[],
                    can_share=bool(key),
                ),
            )["rows"].append(row)
    else:
        by_id = {p.pk: p for p in packages}
        for package in sorted(packages, key=lambda p: category_key(p.title)):
            groups[package.pk] = dict(
                label=package.title,
                category=package.pk,
                amount=package.allowance_amount,
                rows=[],
                can_share=True,
            )
        for row in rows:
            if row.package_id:
                row.package = by_id[row.package_id]
            groups.setdefault(
                row.package_id,
                dict(
                    label="Uncategorized",
                    category=None,
                    amount=None,
                    rows=[],
                    can_share=False,
                ),
            )["rows"].append(row)
    version = hashlib.sha256(
        json.dumps(
            [
                [
                    (
                        r.pk,
                        r.category if template else r.package_id,
                        r.allowance_mode,
                        str(r.allowance_amount),
                        getattr(r, "status", ""),
                        str(getattr(r, "updated_at", "")),
                    )
                    for r in rows
                ],
                [
                    (p.pk, p.category if template else p.title, str(p.allowance_amount))
                    for p in packages
                ],
                str(owner.updated_at),
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return list(groups.values()), version


class AllowanceSetupForm(forms.Form):
    version = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, groups, version, template=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.groups, self.template = groups, template
        self.fields["version"].initial = version
        self.layout = []
        for index, group in enumerate(groups):
            enabled_key, amount_key = (
                f"section_{index}_enabled",
                f"section_{index}_amount",
            )
            self.fields[enabled_key] = forms.BooleanField(
                required=False,
                initial=group["amount"] is not None,
                label="Use a shared section allowance",
                disabled=not group["can_share"],
            )
            self.fields[amount_key] = forms.DecimalField(
                required=False,
                min_value=0,
                max_digits=12,
                decimal_places=2,
                initial=group["amount"],
                label="Section allowance",
                widget=forms.NumberInput(attrs={"step": "0.01"}),
                disabled=not group["can_share"],
            )
            entries = []
            for row in group["rows"]:
                mode_key, own_key = f"row_{row.pk}_mode", f"row_{row.pk}_amount"
                self.fields[mode_key] = forms.ChoiceField(
                    choices=AllowanceMode.choices,
                    initial=row.allowance_mode,
                    label=f"{row.title}: allowance source",
                )
                self.fields[own_key] = forms.DecimalField(
                    required=False,
                    min_value=0,
                    max_digits=12,
                    decimal_places=2,
                    initial=row.allowance_amount,
                    label=f"{row.title}: own allowance",
                    widget=forms.NumberInput(attrs={"step": "0.01"}),
                )
                entries.append(dict(row=row, mode=self[mode_key], amount=self[own_key]))
            self.layout.append(
                dict(
                    label=group["label"],
                    enabled=self[enabled_key],
                    amount=self[amount_key],
                    rows=entries,
                )
            )

    def clean(self):
        data = super().clean()
        for index, group in enumerate(self.groups):
            enabled = data.get(f"section_{index}_enabled")
            amount_key = f"section_{index}_amount"
            if enabled and data.get(amount_key) is None:
                self.add_error(amount_key, "Enter the shared allowance amount.")
            active_rows = [
                row for row in group["rows"] if getattr(row, "status", None) != "voided"
            ]
            if (
                enabled
                and active_rows
                and not any(
                    data.get(f"row_{row.pk}_mode") == AllowanceMode.SECTION
                    for row in active_rows
                )
            ):
                self.add_error(
                    f"section_{index}_enabled",
                    "No selections use this section allowance. Choose Use section allowance "
                    "for the items it covers, or turn off the shared allowance. "
                    "Items with Own allowance do not draw from this budget.",
                )
            for row in group["rows"]:
                mode_key, own_key = f"row_{row.pk}_mode", f"row_{row.pk}_amount"
                mode, own = data.get(mode_key), data.get(own_key)
                if mode == AllowanceMode.SECTION and not enabled:
                    self.add_error(
                        mode_key,
                        "Enable this section allowance first, or choose Own allowance / No allowance.",
                    )
                if mode == AllowanceMode.INDIVIDUAL and own is None:
                    self.add_error(
                        own_key,
                        "Enter an individual allowance, including zero for a fully additional item.",
                    )
                if (
                    not self.template
                    and (
                        mode != row.allowance_mode
                        or (mode == "individual" and own != row.allowance_amount)
                    )
                    and financial_links(row)
                ):
                    self.add_error(
                        mode_key,
                        "This item has billing or credit records. Review those records before changing its allowance basis.",
                    )
        return data


@login_required
@require_http_methods(["GET", "POST"])
def project_allowances(request, pk):
    from .views import managed_project_or_404

    owner = managed_project_or_404(request.user, pk)
    return allowance_editor(request, owner, template=False)


@login_required
@require_http_methods(["GET", "POST"])
def template_allowances(request, slug, template_pk):
    from .selection_template_views import template_for

    owner = template_for(request.user, slug, template_pk)
    return allowance_editor(request, owner, template=True)


def allowance_editor(request, owner, *, template):
    back = (
        reverse(
            "projects:selection_template_detail",
            args=[owner.organization.slug, owner.pk],
        )
        if template
        else reverse("projects:selection_list", args=[owner.pk])
    )
    with transaction.atomic():
        if request.method == "POST":
            owner = (
                (SelectionTemplate if template else Project)
                .objects.select_for_update()
                .get(pk=owner.pk)
            )
        groups, version = setup_state(owner, template, lock=request.method == "POST")
        form = AllowanceSetupForm(
            request.POST if request.method == "POST" else None,
            groups=groups,
            version=version,
            template=template,
        )
        if request.method == "POST" and form.is_valid():
            if form.cleaned_data["version"] != version:
                form.add_error(
                    None,
                    "Selections or allowances changed in another window. Reload this page before saving; nothing was changed.",
                )
            else:
                changes = []
                for index, group in enumerate(groups):
                    amount = (
                        form.cleaned_data[f"section_{index}_amount"]
                        if form.cleaned_data[f"section_{index}_enabled"]
                        else None
                    )
                    if amount != group["amount"]:
                        changes.append(
                            dict(
                                section=group["label"],
                                before=str(group["amount"]),
                                after=str(amount),
                            )
                        )
                    if template:
                        if amount is None:
                            owner.allowance_sections.filter(
                                category__iexact=group["category"]
                            ).delete()
                        else:
                            section = owner.allowance_sections.filter(
                                category__iexact=group["category"]
                            ).first()
                            if section:
                                section.allowance_amount = amount
                                section.save(update_fields=["allowance_amount"])
                            else:
                                SelectionTemplateSection.objects.create(
                                    template=owner,
                                    category=group["category"],
                                    allowance_amount=amount,
                                )
                    elif group["category"]:
                        owner.selection_packages.filter(pk=group["category"]).update(
                            allowance_amount=amount
                        )
                    for row in group["rows"]:
                        mode = form.cleaned_data[f"row_{row.pk}_mode"]
                        own = form.cleaned_data[f"row_{row.pk}_amount"]
                        # Preserve dormant individual values when switching to a pool or no allowance.
                        own = (
                            own
                            if mode == AllowanceMode.INDIVIDUAL
                            else row.allowance_amount
                        )
                        if (row.allowance_mode, row.allowance_amount) != (mode, own):
                            changes.append(
                                dict(
                                    selection_id=row.pk,
                                    before=[
                                        row.allowance_mode,
                                        str(row.allowance_amount),
                                    ],
                                    after=[mode, str(own)],
                                )
                            )
                            row.allowance_mode, row.allowance_amount = mode, own
                            row.save(
                                update_fields=["allowance_mode", "allowance_amount"]
                                + ([] if template else ["updated_at"])
                            )
                owner.save(update_fields=["updated_at"])
                record_activity(
                    organization=owner.organization,
                    project=None if template else owner,
                    actor=request.user,
                    event_type=ActivityEvent.Type.SELECTION_UPDATED,
                    summary="Updated selection allowance settings.",
                    metadata={
                        "template_id": owner.pk if template else None,
                        "changes": changes,
                    },
                )
                messages.success(
                    request,
                    "Allowances saved. Section budgets are counted once; individual and no-allowance items are excluded from the shared budget.",
                )
                return redirect(back)
    return render(
        request,
        "projects/allowance_setup.html",
        dict(form=form, owner=owner, back_url=back, is_template=template),
    )
