import csv
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from projects.client_preview import SESSION_KEY
from projects.models import (
    ChangeOrder,
    ChangeOrderLineItem,
    FinishSelection,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
    SelectionOption,
    SelectionPackage,
)
from projects.reconciliation import project_reconciliation
from projects.reconciliation_export import reconciliation_csv
from projects.tests import grant_internal_access


class ReconciliationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Builder", slug="builder")
        self.admin = get_user_model().objects.create_user(
            "admin@example.test", "password"
        )
        self.customer = get_user_model().objects.create_user(
            "client@example.test", "password"
        )
        self.office = get_user_model().objects.create_user(
            "office@example.test", "password"
        )
        for user, role in (
            (self.admin, "admin"),
            (self.customer, "client"),
            (self.office, "office_manager"),
        ):
            OrganizationMembership.objects.create(
                organization=self.org, user=user, role=role
            )
        self.project = Project.objects.create(
            organization=self.org, name="Customer home", contract_amount=100000
        )
        self.foreign = Project.objects.create(
            organization=self.org, name="Other client home"
        )
        ProjectMembership.objects.create(
            project=self.project, user=self.customer, role="client"
        )
        grant_internal_access(self.office, self.project, can_manage=False)
        self.number = 0
        self.order_number = 0
        self.url = reverse("projects:reconciliation", args=[self.project.pk])

    def selection(self, title, price, allowance, *, status="selected", project=None):
        project = project or self.project
        self.number += 1
        package, _ = SelectionPackage.objects.get_or_create(
            project=project, title=title, defaults={"created_by": self.admin}
        )
        item = FinishSelection.objects.create(
            project=project,
            number=self.number,
            title=title,
            package=package,
            allowance_amount=Decimal(allowance),
            created_by=self.admin,
            description="PRIVATE SELECTION DRAFT NOTES",
        )
        item.status = status
        if status in ("open", "selected", "voided"):
            item.opened_by = self.admin
            item.opened_at = timezone.now()
        if status == "selected":
            option = SelectionOption.objects.create(
                selection=item,
                name="Client choice",
                price=Decimal(price),
                cost=87654.32,
                vendor_name="PRIVATE VENDOR",
            )
            item.chosen_option = option
            item.selected_by = self.customer
            item.selected_at = timezone.now()
        elif status == "imported":
            item.imported_total = Decimal(price)
            item.import_source = {"source_key": str(self.number)}
        elif status == "voided":
            item.voided_by = self.admin
            item.voided_at = timezone.now()
        item.save()
        return item

    def order(
        self, amount, *, status="approved", title="Scope change", source=None, lines=()
    ):
        self.order_number += 1
        data = dict(
            project=self.project,
            number=self.order_number,
            title=title,
            description="PRIVATE REASON NOT IN REPORT",
            price_delta=Decimal(amount),
            cost_delta=76543.21,
            created_by=self.admin,
            status=status,
            source_selection=source,
        )
        if status not in ("draft", "imported"):
            data.update(submitted_by=self.admin, submitted_at=timezone.now())
        if status in ("approved", "declined"):
            data.update(decided_by=self.customer, decided_at=timezone.now())
        if status == "voided":
            data.update(voided_by=self.admin, voided_at=timezone.now())
        if status == "imported":
            data["import_source"] = {"source_key": str(self.order_number)}
        order = ChangeOrder.objects.create(**data)
        for description, quantity, price in lines:
            ChangeOrderLineItem.objects.create(
                change_order=order,
                description=description,
                quantity=quantity,
                unit_price=price,
                unit_cost=12345.67,
            )
        return order

    def test_original_adjustment_and_new_total_with_line_items(self):
        self.order("5000", lines=[("Windows", 2, 1500), ("Doors", 1, 2000)])
        self.selection("Cabinetry", "23000", "20000")
        self.selection("Lighting", "1800", "2000")
        report = project_reconciliation(self.project)
        self.assertEqual(report["approved_total"], Decimal("5000"))
        self.assertEqual(report["selection_total"], Decimal("2800"))
        self.assertEqual(report["adjustment"], Decimal("7800"))
        self.assertEqual(report["new_total"], Decimal("107800"))
        self.assertEqual(len(report["approved_orders"][0]["lines"]), 2)

    def test_partial_linked_credit_is_counted_once(self):
        credit = self.selection("Cabinetry", "18000", "20000")
        self.order("-500", source=credit)
        report = project_reconciliation(self.project)
        self.assertEqual(report["linked_total"], Decimal("-500"))
        self.assertEqual(report["selection_total"], Decimal("-1500"))
        self.assertEqual(report["adjustment"], Decimal("-2000"))
        self.assertEqual(report["new_total"], Decimal("98000"))
        self.assertEqual(
            report["approved_orders"][0]["lines"][0]["price"], Decimal("-500")
        )

    def test_pending_links_do_not_remove_credit_from_current_total(self):
        credit = self.selection("Cabinetry", "18000", "20000")
        self.order("-2000", status="pending", source=credit)
        report = project_reconciliation(self.project)
        self.assertEqual(report["selection_total"], Decimal("-2000"))
        self.assertEqual(report["approved_total"], 0)
        self.assertEqual(report["pending_total"], Decimal("-2000"))

    def test_zero_sections_omitted_but_zero_price_with_allowance_is_credit(self):
        self.selection("No cost", "0", "0")
        self.selection("Unused allowance", "0", "500")
        self.selection("Not chosen", None, "20000", status="open")
        report = project_reconciliation(self.project)
        self.assertEqual([s["label"] for s in report["sections"]], ["Unused allowance"])
        self.assertEqual(report["adjustment"], Decimal("-500"))
        self.assertEqual(report["unpriced_allowance"], Decimal("20000"))

    def test_priced_section_with_offsetting_lines_is_retained(self):
        self.selection("Cabinetry", "100", "0")
        self.selection("Cabinetry", "-100", "0")
        report = project_reconciliation(self.project)
        self.assertEqual(len(report["sections"]), 1)
        self.assertEqual(report["sections"][0]["price"], 0)

    def test_section_numbers_sort_naturally(self):
        for label in (
            "10 - Fixtures",
            "06.1 - Shower",
            "06 - Plumbing",
            "02 - Kitchen",
        ):
            self.selection(label, "1", "0")
        report = project_reconciliation(self.project)
        self.assertEqual(
            [s["label"] for s in report["sections"]],
            ["02 - Kitchen", "06 - Plumbing", "06.1 - Shower", "10 - Fixtures"],
        )

    def test_imported_prices_signed_credits_and_recorded_order_total(self):
        self.order("250", status="imported", lines=[("Imported item", 1, 200)])
        self.selection("Imported credit", "-100", "0", status="imported")
        report = project_reconciliation(self.project)
        self.assertEqual(report["adjustment"], Decimal("150"))
        self.assertEqual(
            sum(line["price"] for line in report["approved_orders"][0]["lines"]),
            Decimal("250"),
        )
        self.assertIn(
            "Adjustment to recorded order total",
            report["approved_orders"][0]["lines"][1]["description"],
        )

    def test_drafts_declined_voided_archived_and_other_project_excluded(self):
        self.order("9000", status="draft", title="SECRET DRAFT ORDER")
        self.order("8000", status="declined")
        self.order("7000", status="voided")
        self.selection("SECRET DRAFT SELECTION", None, "6000", status="draft")
        self.selection("Voided", None, "5000", status="voided")
        archived = self.selection("Archived", None, "4000", status="draft")
        archived.archived_at = timezone.now()
        archived.save()
        self.selection("Other project", "3000", "0", project=self.foreign)
        report = project_reconciliation(self.project)
        self.assertEqual(report["adjustment"], 0)
        self.assertEqual(report["unpriced_count"], 0)
        self.assertEqual(report["pending_orders"], [])
        self.assertEqual(
            project_reconciliation(self.project, client_view=False)["pending_total"],
            Decimal("9000"),
        )

    def test_no_contract_stays_unknown_and_zero_contract_is_valid(self):
        self.project.contract_amount = None
        self.assertIsNone(project_reconciliation(self.project)["new_total"])
        self.project.contract_amount = Decimal("0")
        self.assertEqual(
            project_reconciliation(self.project)["new_total"], Decimal("0")
        )

    def test_client_html_and_csv_hide_internal_data_and_drafts(self):
        self.order("500", lines=[("Visible client item", 1, 500)])
        self.order("999999", status="draft", title="SECRET DRAFT ORDER")
        self.selection("Visible section", "100", "50")
        self.selection("SECRET DRAFT SELECTION", None, "888888", status="draft")
        self.client.force_login(self.customer)
        for query in ({}, {"download": "csv"}):
            response = self.client.get(self.url, query)
            self.assertEqual(response.status_code, 200)
            content = response.content.decode("utf-8")
            for secret in (
                "SECRET DRAFT",
                "PRIVATE",
                "87654",
                "87,654",
                "76543",
                "76,543",
                "12345",
                "12,345",
                "999999",
                "888888",
            ):
                self.assertNotIn(secret, content)
            self.assertIn("Visible client item", content)
            self.assertIn("Visible section", content)
            self.assertIn("no-store", response["Cache-Control"])
        self.assertContains(
            self.client.get(reverse("projects:detail", args=[self.project.pk])),
            "Allowances &amp; project totals",
        )

    def test_access_is_project_scoped_and_denied_when_revoked(self):
        self.client.force_login(self.customer)
        for query in ({}, {"download": "csv"}):
            self.assertEqual(
                self.client.get(
                    reverse("projects:reconciliation", args=[self.foreign.pk]), query
                ).status_code,
                404,
            )
        self.project.project_memberships.update(is_active=False)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_authorized_signer_has_no_general_financial_access(self):
        self.project.project_memberships.update(client_access_type="signer")
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_staff_financial_permission_and_client_preview(self):
        self.client.force_login(self.office)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        membership = self.office.organization_memberships.get()
        membership.is_active = False
        membership.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.order("9999", status="draft", title="SECRET DRAFT ORDER")
        self.client.force_login(self.admin)
        session = self.client.session
        session[SESSION_KEY] = self.project.pk
        session.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "SECRET DRAFT ORDER")
        response = self.client.get(self.url, {"download": "csv"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("SECRET DRAFT ORDER", response.content.decode())

    def test_csv_formula_protection_keeps_credit_amounts_numeric(self):
        self.project.name = '=HYPERLINK("malicious")'
        self.order("-500", title="+malicious")
        self.selection("@malicious", "-100", "0")
        content = reconciliation_csv(self.project, project_reconciliation(self.project))
        rows = list(csv.reader(StringIO(content.lstrip("\ufeff"))))
        self.assertTrue(rows[0][1].startswith("'="))
        self.assertIn("'+malicious", content)
        self.assertIn("'@malicious", content)
        self.assertIn(["Net adjustment", "-600.00"], rows)

    def test_read_only_page_never_creates_accounting_records(self):
        from billing.models import CreditMemo, Invoice

        self.selection("Cabinetry", "23000", "20000")
        self.client.force_login(self.customer)
        self.client.get(self.url)
        self.client.get(self.url, {"download": "csv"})
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(CreditMemo.objects.exists())
        self.assertFalse(ChangeOrder.objects.exists())
        self.assertEqual(self.client.post(self.url, {}).status_code, 405)
