from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from projects.models import (
    FinishSelection,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
    SelectionOption,
    SelectionPackage,
)
from projects.selection_board import board_context, selection_totals


class SelectionTotalsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Builder", slug="builder")
        self.admin = get_user_model().objects.create_user(
            "admin@example.test", "password"
        )
        self.customer = get_user_model().objects.create_user(
            "client@example.test", "password"
        )
        OrganizationMembership.objects.create(
            organization=self.org, user=self.admin, role="admin"
        )
        OrganizationMembership.objects.create(
            organization=self.org, user=self.customer, role="client"
        )
        self.project = Project.objects.create(organization=self.org, name="House")
        ProjectMembership.objects.create(
            project=self.project, user=self.customer, role="client"
        )
        self.category = SelectionPackage.objects.create(
            project=self.project, title="Cabinetry", created_by=self.admin
        )
        self.counter = 0

    def row(self, title, allowance, price=None, status="selected", category=True):
        self.counter += 1
        row = FinishSelection.objects.create(
            project=self.project,
            number=self.counter,
            title=title,
            created_by=self.admin,
            package=self.category if category else None,
            allowance_amount=Decimal(allowance),
            status="draft",
        )
        row.status = status
        if status != "draft":
            row.opened_by = self.admin
            row.opened_at = timezone.now()
        if status == "voided":
            row.voided_by = self.admin
            row.voided_at = timezone.now()
        if price is not None and status == "selected":
            option = SelectionOption.objects.create(
                selection=row,
                name="Chosen finish",
                price=Decimal(price),
                cost=Decimal("87654.32"),
            )
            row.chosen_option = option
            row.selected_by = self.customer
            row.selected_at = timezone.now()
        row.save()
        return row

    def test_subtotals_and_grand_total_only_compare_priced_allowances(self):
        self.row("Cabinets", "20000", "23000")
        self.row("Hardware", "1000", "750")
        self.row("Not decided", "50000", status="open")
        self.row("Credit item", "0", "-500", category=False)
        context = board_context(self.project, self.admin, {})
        total = context["board_totals"]
        self.assertEqual(total["price"], Decimal("23250"))
        self.assertEqual(total["remaining"], Decimal("-2250"))
        self.assertEqual(total["allowance"], Decimal("71000"))
        self.assertEqual(total["pending_allowance"], Decimal("50000"))
        self.assertEqual(total["priced_count"], 3)
        groups = {label: totals for label, rows, totals in context["total_groups"]}
        self.assertEqual(groups["Cabinetry"]["remaining"], Decimal("-2750"))

    def test_client_totals_never_include_hidden_drafts_or_other_projects(self):
        self.row("Visible", "1000", "700")
        self.row("Internal draft", "9000", status="draft")
        other = Project.objects.create(organization=self.org, name="Other house")
        FinishSelection.objects.create(
            project=other,
            number=1,
            title="Other allowance",
            allowance_amount=999999,
            created_by=self.admin,
        )
        context = board_context(self.project, self.customer, {})
        self.assertEqual(context["board_totals"]["allowance"], Decimal("1000"))
        self.assertEqual(context["board_totals"]["remaining"], Decimal("300"))
        self.client.force_login(self.customer)
        response = self.client.get(
            reverse("projects:selection_list", args=[self.project.pk])
        )
        self.assertContains(response, "Grand total")
        self.assertNotContains(response, "Internal draft")
        self.assertNotContains(response, "87654.32")

    def test_archived_and_voided_rows_are_excluded(self):
        self.row("Valid", "100", "100")
        voided = self.row("Expired", "9000", "9000", status="voided")
        archived = self.row("Deleted", "8000", status="draft")
        archived.archived_at = timezone.now()
        archived.save()
        total = board_context(self.project, self.admin, {})["board_totals"]
        self.assertEqual(total["price"], Decimal("100"))
        self.assertEqual(total["allowance"], Decimal("100"))
        self.assertEqual(total["excluded_count"], 1)
        self.assertEqual(voided.status, "voided")

    def test_filters_location_and_cards_total_visible_rows(self):
        row = self.row("Cabinets", "20000", "18000")
        row.location = "Kitchen"
        row.save()
        self.row("Hardware", "1000", "1100")
        self.client.force_login(self.admin)
        for view in ("list", "cards"):
            response = self.client.get(
                reverse("projects:selection_list", args=[self.project.pk]),
                {"q": "Cabinets", "group": "location", "view": view},
            )
            self.assertEqual(
                response.context["board_totals"]["remaining"], Decimal("2000")
            )
            self.assertContains(response, "Kitchen subtotal")
            self.assertContains(response, "Totals cover the filtered selections shown.")
            self.assertContains(response, "selection-positive")

    def test_zero_price_is_priced_and_empty_is_not_a_credit(self):
        total = selection_totals(
            [
                SimpleNamespace(
                    status="selected",
                    board_price=Decimal("0"),
                    allowance_amount=Decimal("100"),
                )
            ]
        )
        self.assertEqual(total["priced_count"], 1)
        self.assertEqual(total["remaining"], Decimal("100"))
        self.assertEqual(selection_totals([])["priced_count"], 0)
        self.assertEqual(selection_totals([])["remaining"], 0)

    def test_decimal_cents_are_exact(self):
        total = selection_totals(
            [
                SimpleNamespace(
                    status="selected",
                    board_price=Decimal("0.10"),
                    allowance_amount=Decimal("0.30"),
                )
                for _ in range(3)
            ]
        )
        self.assertEqual(total["price"], Decimal("0.30"))
        self.assertEqual(total["remaining"], Decimal("0.60"))
