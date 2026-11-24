import json
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from projects.allowance_groups import validate_groups
from projects.allowance_views import setup_state
from projects.financials import project_financial_summary
from projects.forms import FinishSelectionForm
from projects.project_lifecycle import copy_setup
from projects.reconciliation import project_reconciliation
from projects.reconciliation_export import reconciliation_csv
from projects.selection_board import board_context
from projects.selection_templates import (
    copy_template,
    load_template,
    save_project_template,
)
from projects.tests.test_reconciliation import ReconciliationTests


class AllowanceGroupTests(TestCase):
    setUp = ReconciliationTests.setUp
    selection = ReconciliationTests.selection

    def grouped(self):
        windows = self.selection("Windows", "27000", "0")
        doors = self.selection("Doors", "14000", "0")
        package = windows.package
        package.title = "Windows & Doors"
        groups = [
            dict(id=str(uuid4()), name="Windows", amount="25000.00"),
            dict(id=str(uuid4()), name="Doors", amount="15000.00"),
        ]
        package.allowance_groups, package.allowance_amount = groups, 40000
        package.save()
        for row, group in zip((windows, doors), groups):
            row.package = package
            row.allowance_mode, row.allowance_group = "section", group["id"]
            row.save()
        return windows, doors

    def payload(self, owner=None, template=False):
        groups, version = setup_state(owner or self.project, template=template)
        data = {"version": version}
        for i, group in enumerate(groups):
            data[f"section_{i}_groups"] = json.dumps(group.get("allowance_groups", []))
            if group["amount"] is not None:
                data[f"section_{i}_enabled"] = "on"
                data[f"section_{i}_amount"] = str(group["amount"])
            for row in group["rows"]:
                data[f"row_{row.pk}_mode"] = row.allowance_mode
                data[f"row_{row.pk}_group"] = row.allowance_group
                data[f"row_{row.pk}_amount"] = str(row.allowance_amount)
        return data

    def test_group_totals_roll_up_once_for_staff_client_and_export(self):
        windows, doors = self.grouped()
        for viewer in (self.admin, self.customer):
            totals = board_context(self.project, viewer, {})["board_totals"]
            self.assertEqual(
                (totals["price"], totals["allowance"], totals["remaining"]),
                (41000, 40000, -1000),
            )
            balances = {pool["label"]: pool["remaining"] for pool in totals["shared"]}
            self.assertEqual(balances, {"Windows": -2000, "Doors": 1000})
        report = project_reconciliation(self.project)
        self.assertEqual(report["selection_total"], 1000)
        self.assertEqual(report["new_total"], 101000)
        flags = project_financial_summary(self.project)
        self.assertEqual(flags["selection_overage_total"], 2000)
        self.assertEqual(flags["selection_credit_total"], 1000)
        self.assertIn("Windows", reconciliation_csv(self.project, report))
        self.client.force_login(self.customer)
        page = self.client.get(self.url)
        self.assertContains(page, "Uses Windows")
        self.assertContains(page, "Uses Doors")
        self.assertNotContains(page, "87654.32")

    def test_multiple_rows_use_one_group_budget_and_exceptions_stay_separate(self):
        windows, doors = self.grouped()
        accessory = self.selection("Window accessory", "500", "0")
        accessory.package, accessory.allowance_mode, accessory.allowance_group = (
            windows.package,
            "section",
            windows.allowance_group,
        )
        accessory.save()
        own = self.selection("Own hardware", "800", "1000")
        own.package = windows.package
        own.save()
        included = self.selection("Installation", "5000", "0")
        included.package, included.allowance_mode = windows.package, "none"
        included.save()
        total = board_context(self.project, self.admin, {})["board_totals"]
        self.assertEqual(
            (total["price"], total["allowance"], total["remaining"]),
            (47300, 41000, -1300),
        )
        self.assertEqual(project_reconciliation(self.project)["selection_total"], 1300)

    def test_hidden_pending_blocks_only_its_group_credit(self):
        windows, doors = self.grouped()
        pending = self.selection("SECRET WINDOW DRAFT", "0", "0", status="draft")
        pending.package, pending.allowance_mode, pending.allowance_group = (
            windows.package,
            "section",
            windows.allowance_group,
        )
        pending.save()
        windows.chosen_option.price = 23000
        windows.chosen_option.save()
        report = project_reconciliation(self.project)
        self.assertEqual(
            report["selection_total"], -1000
        )  # Only the completed Doors group.
        self.client.force_login(self.customer)
        page = self.client.get(self.url)
        self.assertNotContains(page, "SECRET WINDOW DRAFT")
        self.assertContains(page, "Choices still pending")

    def test_filtered_group_keeps_complete_budget_but_partial_group_omits_it(self):
        windows, doors = self.grouped()
        windows.title = "Glazing"
        windows.save()
        total = board_context(self.project, self.admin, {"q": "Glazing"})[
            "board_totals"
        ]
        self.assertEqual((total["allowance"], total["remaining"]), (25000, -2000))
        more = self.selection("Window accessories", "100", "0")
        more.package, more.allowance_mode, more.allowance_group = (
            windows.package,
            "section",
            windows.allowance_group,
        )
        more.save()
        total = board_context(self.project, self.admin, {"q": "Glazing"})[
            "board_totals"
        ]
        self.assertTrue(total["partial_pool"])
        self.assertEqual(total["allowance"], 0)

    def test_setup_derives_parent_and_renames_without_losing_assignments(self):
        windows, doors = self.grouped()
        self.client.force_login(self.admin)
        url = reverse("projects:selection_allowances", args=[self.project.pk])
        data = self.payload()
        groups, _ = setup_state(self.project)
        index = next(
            i for i, g in enumerate(groups) if g["category"] == windows.package_id
        )
        budgets = windows.package.allowance_groups
        budgets[0]["name"], budgets[0]["amount"] = "Exterior windows", "26000.00"
        data[f"section_{index}_groups"] = json.dumps(budgets)
        data[f"section_{index}_amount"] = "999999"  # Never trusted as the group total.
        self.assertEqual(self.client.post(url, data).status_code, 302)
        windows.refresh_from_db()
        windows.package.refresh_from_db()
        self.assertEqual(windows.package.allowance_amount, 41000)
        self.assertEqual(windows.allowance_description, "Uses Exterior windows")
        self.assertContains(self.client.post(url, data), "Reload this page")

    def test_removing_or_forging_assigned_group_is_rejected_atomically(self):
        windows, doors = self.grouped()
        self.client.force_login(self.admin)
        url = reverse("projects:selection_allowances", args=[self.project.pk])
        data = self.payload()
        data[f"row_{windows.pk}_group"] = str(uuid4())
        self.assertContains(self.client.post(url, data), "Choose the allowance group")
        windows.refresh_from_db()
        self.assertEqual(
            windows.allowance_group, windows.package.allowance_groups[0]["id"]
        )

    def test_template_and_project_copy_keep_budgets_and_reset_decisions(self):
        windows, doors = self.grouped()
        template = save_project_template(
            self.project,
            self.admin,
            "Windows and doors",
            "",
            self.project.finish_selections.all(),
        )
        copied = copy_template(template, self.admin, "Copy")
        self.assertEqual(
            copied.allowance_sections.get().allowance_groups,
            windows.package.allowance_groups,
        )
        load_template(copied, self.foreign, self.admin, copied.rows.all())
        self.assertEqual(
            self.foreign.selection_packages.get().allowance_groups,
            windows.package.allowance_groups,
        )
        self.assertEqual(
            set(
                self.foreign.finish_selections.values_list("allowance_group", flat=True)
            ),
            {windows.allowance_group, doors.allowance_group},
        )
        self.assertFalse(
            self.foreign.finish_selections.exclude(status="draft").exists()
        )
        target = copy_setup(
            self.project,
            self.admin,
            dict(
                name="Copy",
                code="",
                selections=self.project.finish_selections.all(),
                choices=True,
                photos=False,
            ),
        )
        self.assertEqual(
            target.selection_packages.get().allowance_groups,
            windows.package.allowance_groups,
        )

    def test_group_schema_rejects_duplicate_names_negative_and_bad_payload(self):
        group = dict(id=str(uuid4()), name="Windows", amount="10.00")
        for value in (
            {},
            [dict(group, amount="-1")],
            [dict(group, amount="NaN")],
            [group, dict(group, id=str(uuid4()), name=" windows ")],
            [dict(group, amount="0.001")],
            [dict(group, id="wrong")],
        ):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_groups(value)

    def test_zero_and_credit_prices_in_group(self):
        windows, doors = self.grouped()
        windows.chosen_option.price = Decimal("-500")
        windows.chosen_option.save()
        doors.chosen_option.price = 0
        doors.chosen_option.save()
        self.assertEqual(
            project_reconciliation(self.project)["selection_total"], -40500
        )

    def test_partial_copy_brings_only_referenced_group_budget(self):
        windows, doors = self.grouped()
        chosen = self.project.finish_selections.filter(pk=windows.pk)
        template = save_project_template(
            self.project, self.admin, "Windows only", "", chosen
        )
        self.assertEqual(template.allowance_sections.get().allowance_amount, 25000)
        self.assertEqual(len(template.allowance_sections.get().allowance_groups), 1)
        target = copy_setup(
            self.project,
            self.admin,
            dict(
                name="Partial", code="", selections=chosen, choices=True, photos=False
            ),
        )
        self.assertEqual(target.selection_packages.get().allowance_amount, 25000)

    def test_selection_form_rejects_group_from_other_category(self):
        windows, doors = self.grouped()
        other = self.selection("Other category", "10", "0", status="draft")
        form = FinishSelectionForm(
            dict(
                title="Other category",
                package=other.package_id,
                allowance_mode="section",
                allowance_group=windows.allowance_group,
                allowance_amount="0",
            ),
            project=self.project,
            instance=other,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("allowance_group", form.errors)

    def test_template_editor_saves_groups_and_rejects_removed_assignment(self):
        windows, doors = self.grouped()
        template = save_project_template(
            self.project,
            self.admin,
            "Reusable",
            "",
            self.project.finish_selections.all(),
        )
        self.client.force_login(self.admin)
        url = reverse(
            "projects:selection_template_allowances", args=[self.org.slug, template.pk]
        )
        data = self.payload(template, template=True)
        groups = template.allowance_sections.get().allowance_groups
        groups[0]["amount"] = "26000.00"
        data["section_0_groups"] = json.dumps(groups)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(template.allowance_sections.get().allowance_amount, 41000)
        template.refresh_from_db()
        data = self.payload(template, template=True)
        data["section_0_groups"] = json.dumps(groups[1:])
        self.assertContains(self.client.post(url, data), "Choose the allowance group")
        self.assertEqual(len(template.allowance_sections.get().allowance_groups), 2)
