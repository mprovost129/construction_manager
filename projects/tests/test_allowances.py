from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from billing.services import create_invoice_from_selection
from projects.allowance_views import AllowanceSetupForm, setup_state
from projects.client_preview import SESSION_KEY
from projects.financials import project_financial_summary
from projects.models import (
    ActivityEvent,
    SelectionTemplateSection,
)
from projects.project_lifecycle import copy_setup
from projects.reconciliation import project_reconciliation
from projects.reconciliation_export import reconciliation_csv
from projects.selection_board import board_context
from projects.selection_templates import (
    copy_template,
    load_template,
    save_project_template,
)
from projects.services import format_allowance_variance
from projects.tests.test_reconciliation import ReconciliationTests


class SharedAllowanceTests(TestCase):
    setUp = ReconciliationTests.setUp
    selection = ReconciliationTests.selection
    order = ReconciliationTests.order

    def shared(self, title, price, *, package=None, status="selected"):
        row = self.selection(title, price, "999", status=status)
        if package:
            row.package = package
        row.allowance_mode = "section"
        row.package.allowance_amount = Decimal("20000")
        row.package.save()
        row.save()
        return row

    def post_data(self):
        groups, version = setup_state(self.project)
        data = {"version": version}
        for i, group in enumerate(groups):
            if group["amount"] is not None:
                data[f"section_{i}_enabled"] = "on"
                data[f"section_{i}_amount"] = str(group["amount"])
            for row in group["rows"]:
                data[f"row_{row.pk}_mode"] = row.allowance_mode
                data[f"row_{row.pk}_amount"] = str(row.allowance_amount)
        return data

    def test_mixed_pool_individual_and_included_prices_count_budget_once(self):
        cabinets = self.shared("Cabinetry", "15000")
        self.shared("Hardware", "2000", package=cabinets.package)
        separate = self.selection("Separate fixture", "800", "1000")
        separate.package = cabinets.package
        separate.save()
        included = self.selection("Installation", "5000", "0")
        included.package, included.allowance_mode = cabinets.package, "none"
        included.save()
        report = project_reconciliation(self.project)
        self.assertEqual(report["selection_price"], Decimal("22800"))
        self.assertEqual(report["selection_allowance"], Decimal("21000"))
        self.assertEqual(report["selection_total"], Decimal("-3200"))
        self.assertEqual(report["new_total"], Decimal("96800"))
        total = board_context(self.project, self.customer, {})["board_totals"]
        self.assertEqual(total["allowance"], Decimal("21000"))
        self.assertEqual(total["included_price"], Decimal("5000"))
        self.assertEqual(total["remaining"], Decimal("3200"))
        self.assertEqual(total["shared"][0]["remaining"], Decimal("3000"))
        self.assertIsNone(cabinets.selected_variance)
        self.assertFalse(cabinets.has_credit)
        self.assertIsNone(included.chosen_option.allowance_variance)
        self.assertEqual(
            format_allowance_variance(included.chosen_option), "Included in contract"
        )
        flags = project_financial_summary(self.project)
        self.assertEqual(flags["selection_credit_total"], Decimal("3200"))

    def test_incomplete_shared_budget_is_remaining_not_final_credit(self):
        selected = self.shared("Cabinetry", "15000")
        self.shared("Hardware", "0", package=selected.package, status="open")
        report = project_reconciliation(self.project)
        self.assertEqual(report["selection_total"], 0)
        self.assertEqual(report["sections"][0]["shared_remaining"], 5000)
        self.assertTrue(report["sections"][0]["provisional"])
        selected.chosen_option.price = 21000
        selected.chosen_option.save()
        self.assertEqual(project_reconciliation(self.project)["selection_total"], 1000)

    def test_hidden_pending_rows_never_become_a_client_credit_or_leak(self):
        row = self.shared("Cabinetry", "15000")
        self.shared("SECRET DRAFT", "0", package=row.package, status="draft")
        self.client.force_login(self.customer)
        response = self.client.get(self.url)
        self.assertEqual(response.context["report"]["selection_total"], 0)
        self.assertNotContains(response, "SECRET DRAFT")
        self.assertNotContains(response, "87654.32")
        self.assertContains(response, "Shared budget still in progress")

    def test_no_allowance_price_visible_without_adjustment_or_allowance_amount(self):
        row = self.selection("Contract item", "1200", "12345")
        row.allowance_mode = "none"
        row.save()
        self.client.force_login(self.customer)
        response = self.client.get(
            reverse("projects:selection_detail", args=[self.project.pk, row.pk])
        )
        self.assertContains(response, "$1200.00")
        for text in (
            "12345",
            "Within allowance",
            "Selected variance",
            "Client allowance",
        ):
            self.assertNotContains(response, text)
        report = project_reconciliation(self.project)
        self.assertEqual(report["selection_total"], 0)
        self.assertEqual(report["selection_price"], 1200)
        self.assertNotIn("12345", reconciliation_csv(self.project, report))
        with self.assertRaisesMessage(ValidationError, "own allowance"):
            create_invoice_from_selection(
                selection_id=row.pk, actor=self.admin, form_data={}
            )

    def test_filter_or_location_slice_does_not_duplicate_shared_budget(self):
        first = self.shared("Cabinetry", "12000")
        second = self.shared("Hardware", "2000", package=first.package)
        first.location, second.location = "Kitchen", "Hall"
        first.save()
        second.save()
        context = board_context(self.project, self.admin, {"group": "location"})
        self.assertEqual(context["board_totals"]["allowance"], 20000)
        for _, _, total in context["total_groups"]:
            self.assertTrue(total["partial_pool"])
            self.assertEqual(total["allowance"], 0)
        filtered = board_context(self.project, self.admin, {"q": "Hardware"})[
            "board_totals"
        ]
        self.assertTrue(filtered["partial_pool"])
        self.assertEqual(filtered["remaining"], 0)

    def test_zero_price_and_negative_credit_use_pool_once(self):
        row = self.shared("Cabinetry", "0")
        self.shared("Credit", "-250", package=row.package)
        report = project_reconciliation(self.project)
        self.assertEqual(report["selection_total"], Decimal("-20250"))

    def test_manage_saves_modes_and_section_budget_with_audit(self):
        row = self.selection("Cabinetry", "15000", "1000")
        self.client.force_login(self.admin)
        data = self.post_data()
        data.update(section_0_enabled="on", section_0_amount="20000")
        data[f"row_{row.pk}_mode"] = "section"
        url = reverse("projects:selection_allowances", args=[self.project.pk])
        self.assertContains(self.client.get(url), "Manage allowances")
        self.assertEqual(self.client.post(url, data).status_code, 302)
        row.refresh_from_db()
        row.package.refresh_from_db()
        self.assertEqual(row.allowance_mode, "section")
        self.assertEqual(row.package.allowance_amount, 20000)
        self.assertEqual(row.allowance_amount, 1000)
        self.assertTrue(
            ActivityEvent.objects.filter(
                summary="Updated selection allowance settings."
            ).exists()
        )
        stale = self.client.post(url, data)
        self.assertContains(stale, "Reload this page")

    def test_manage_rejects_invalid_pool_and_negative_allowance_atomically(self):
        row = self.selection("Cabinetry", "15000", "1000")
        self.client.force_login(self.admin)
        url = reverse("projects:selection_allowances", args=[self.project.pk])
        data = self.post_data()
        data[f"row_{row.pk}_mode"] = "section"
        self.assertContains(
            self.client.post(url, data), "Enable this section allowance first"
        )
        data.update(section_0_enabled="on", section_0_amount="-5")
        self.assertEqual(self.client.post(url, data).status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.allowance_mode, "individual")

    def test_shared_budget_requires_assignment_and_corrects_reported_overage(self):
        cabinets = self.selection("Cabinetry", "34000", "0")
        countertops = self.selection("Countertops", "15000", "0")
        countertops.package = cabinets.package
        countertops.save()
        self.client.force_login(self.admin)
        url = reverse("projects:selection_allowances", args=[self.project.pk])
        data = self.post_data()
        data.update(section_0_enabled="on", section_0_amount="40000")
        response = self.client.post(url, data)
        self.assertContains(response, "No selections use this section allowance")
        self.assertContains(response, "Allowances were not saved")
        cabinets.package.refresh_from_db()
        self.assertIsNone(cabinets.package.allowance_amount)
        self.assertFalse(
            ActivityEvent.objects.filter(
                summary="Updated selection allowance settings."
            ).exists()
        )

        data[f"row_{cabinets.pk}_mode"] = "section"
        data[f"row_{countertops.pk}_mode"] = "section"
        self.assertEqual(self.client.post(url, data).status_code, 302)
        for viewer in (self.admin, self.customer):
            totals = board_context(self.project, viewer, {})["board_totals"]
            self.assertEqual(totals["price"], 49000)
            self.assertEqual(totals["allowance"], 40000)
            self.assertEqual(totals["remaining"], -9000)
        report = project_reconciliation(self.project)
        self.assertEqual(report["selection_total"], 9000)
        self.assertEqual(report["new_total"], 109000)

    def test_template_cannot_save_unused_shared_allowance(self):
        self.selection("Cabinetry", "100", "50", status="draft")
        template = save_project_template(
            self.project,
            self.admin,
            "Standard",
            "",
            self.project.finish_selections.all(),
        )
        row = template.rows.get()
        groups, version = setup_state(template, template=True)
        form = AllowanceSetupForm(
            {
                "version": version,
                "section_0_enabled": "on",
                "section_0_amount": "40000",
                f"row_{row.pk}_mode": "individual",
                f"row_{row.pk}_amount": "50",
            },
            groups=groups,
            version=version,
            template=True,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("No selections use this section allowance", str(form.errors))

    def test_voided_item_does_not_count_as_shared_budget_assignment(self):
        active = self.selection("Active", "100", "0")
        voided = self.selection("Voided", "100", "0", status="voided")
        voided.package = active.package
        voided.allowance_mode = "section"
        voided.save()
        data = self.post_data()
        data.update(section_0_enabled="on", section_0_amount="40000")
        groups, version = setup_state(self.project)
        form = AllowanceSetupForm(data, groups=groups, version=version)
        self.assertFalse(form.is_valid())
        self.assertIn("No selections use this section allowance", str(form.errors))

    def test_clients_preview_and_other_project_cannot_edit_allowances(self):
        self.selection("Cabinetry", "100", "50")
        url = reverse("projects:selection_allowances", args=[self.project.pk])
        for user in (self.customer, self.office):
            self.client.force_login(user)
            self.assertIn(self.client.get(url).status_code, (403, 404))
            self.assertIn(
                self.client.post(url, self.post_data()).status_code, (403, 404)
            )
        self.client.force_login(self.admin)
        session = self.client.session
        session[SESSION_KEY] = self.project.pk
        session.save()
        self.assertIn(self.client.get(url).status_code, (403, 404))

    def test_billed_selection_basis_cannot_be_reinterpreted(self):
        row = self.selection("Cabinetry", "100", "1000")
        create_invoice_from_selection(
            selection_id=row.pk,
            actor=self.admin,
            form_data={
                "title": "Allowance",
                "due_date": None,
                "tax_rate": 0,
                "notes": "",
            },
        )
        self.client.force_login(self.admin)
        data = self.post_data()
        data[f"row_{row.pk}_mode"] = "none"
        response = self.client.post(
            reverse("projects:selection_allowances", args=[self.project.pk]), data
        )
        self.assertContains(response, "billing or credit records")
        row.refresh_from_db()
        self.assertEqual(row.allowance_mode, "individual")

    def test_templates_and_project_copies_preserve_mixed_allowances(self):
        row = self.shared("Cabinetry", "15000")
        included = self.selection("Installation", "2000", "500")
        included.package, included.allowance_mode = row.package, "none"
        included.save()
        template = save_project_template(
            self.project,
            self.admin,
            "Standard",
            "",
            self.project.finish_selections.all(),
        )
        copied = copy_template(template, self.admin, "Copy")
        self.assertEqual(copied.allowance_sections.get().allowance_amount, 20000)
        load_template(copied, self.foreign, self.admin, copied.rows.all())
        self.assertEqual(self.foreign.selection_packages.get().allowance_amount, 20000)
        self.assertEqual(
            set(
                self.foreign.finish_selections.values_list("allowance_mode", flat=True)
            ),
            {"section", "none"},
        )
        self.assertFalse(
            self.foreign.finish_selections.exclude(status="draft").exists()
        )
        target = copy_setup(
            self.project,
            self.admin,
            {
                "name": "New home",
                "code": "",
                "selections": self.project.finish_selections.all(),
                "choices": True,
                "photos": False,
            },
        )
        self.assertEqual(target.selection_packages.get().allowance_amount, 20000)
        self.assertEqual(
            set(target.finish_selections.values_list("allowance_mode", flat=True)),
            {"section", "none"},
        )

    def test_loading_does_not_overwrite_existing_project_pool(self):
        self.shared("Cabinetry", "15000")
        template = save_project_template(
            self.project,
            self.admin,
            "Standard",
            "",
            self.project.finish_selections.all(),
        )
        from projects.models import SelectionPackage

        package = SelectionPackage.objects.create(
            project=self.foreign,
            title="Cabinetry",
            created_by=self.admin,
            allowance_amount=30000,
        )
        load_template(template, self.foreign, self.admin, template.rows.all())
        package.refresh_from_db()
        self.assertEqual(package.allowance_amount, 30000)

    def test_template_allowance_settings_are_company_scoped_and_persist(self):
        self.selection("Cabinetry", "100", "50", status="draft")
        template = save_project_template(
            self.project,
            self.admin,
            "Standard",
            "",
            self.project.finish_selections.all(),
        )
        row = template.rows.get()
        groups, version = setup_state(template, template=True)
        url = reverse(
            "projects:selection_template_allowances", args=[self.org.slug, template.pk]
        )
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url), "Manage allowances")
        data = {
            "version": version,
            "section_0_enabled": "on",
            "section_0_amount": "20000",
            f"row_{row.pk}_mode": "section",
            f"row_{row.pk}_amount": "50",
        }
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(
            SelectionTemplateSection.objects.get(template=template).allowance_amount,
            20000,
        )
        row.refresh_from_db()
        self.assertEqual(row.allowance_mode, "section")
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(url).status_code, 404)
