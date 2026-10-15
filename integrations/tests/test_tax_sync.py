from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice, InvoiceLineItem
from billing.services import issue_invoice
from integrations.invoice_sync import (
    QuickBooksSyncError,
    retry_invoice_sync_attempt,
    start_invoice_sync,
)
from integrations.models import (
    QuickBooksConnection,
    QuickBooksItemMapping,
    QuickBooksProjectCustomerMapping,
    QuickBooksTaxCodeMapping,
)
from integrations.quickbooks import QuickBooksAPIError
from integrations.services import QuickBooksMappingError, save_invoice_mapping
from integrations.taxes import save_tax_mapping, tax_code_options, unlink_tax_mapping
from projects.models import (
    ActivityEvent,
    CostCode,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMembership,
)


@override_settings(QUICKBOOKS_TOKEN_ENCRYPTION_KEYS=(Fernet.generate_key().decode(),))
class QuickBooksTaxTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(email="admin@example.com")
        self.homeowner = get_user_model().objects.create_user(
            email="client@example.com"
        )
        self.org = Organization.objects.create(name="Tax test", slug="tax-test")
        OrganizationMembership.objects.create(
            organization=self.org, user=self.admin, role="admin"
        )
        self.project = Project.objects.create(
            organization=self.org, name="Tax pilot", created_by=self.admin
        )
        ProjectMembership.objects.create(
            project=self.project, user=self.homeowner, role="client"
        )
        self.connection = QuickBooksConnection.objects.create(
            organization=self.org,
            realm_id="123",
            environment="sandbox",
            country="US",
            capabilities={"accounting_write": True},
            capabilities_checked_at=timezone.now(),
        )
        self.code = CostCode.objects.create(
            organization=self.org, code="TAX-TEST", name="Test service"
        )
        QuickBooksProjectCustomerMapping.objects.create(
            project=self.project,
            connection=self.connection,
            quickbooks_customer_id="42",
            quickbooks_display_name="Tax pilot",
        )
        QuickBooksItemMapping.objects.create(
            cost_code=self.code,
            connection=self.connection,
            quickbooks_item_id="1",
            quickbooks_item_name="Service",
        )
        self.api = Mock()
        self.api.get_preferences.return_value = {"TaxPrefs": {"UsingSalesTax": True}}
        self.tax_code = {
            "Id": "2",
            "Name": "Test jurisdiction",
            "Active": True,
            "Taxable": True,
            "TaxGroup": True,
            "SalesTaxRateList": {
                "TaxRateDetail": [
                    {"TaxRateRef": {"value": "3"}, "TaxTypeApplicable": "TaxOnAmount"},
                ]
            },
        }
        self.api.get_tax_code.return_value = self.tax_code
        self.api.get_tax_rate.return_value = {
            "Id": "3",
            "Active": True,
            "RateValue": "8",
        }
        self.api.iter_tax_codes.return_value = [self.tax_code]

    def map_rate(self):
        return save_tax_mapping(
            connection=self.connection,
            local_tax_rate=Decimal("8"),
            code_id="2",
            actor=self.admin,
            api_client=self.api,
        )

    def invoice(self, rate="8", price="100"):
        invoice = Invoice.objects.create(
            organization=self.org,
            project=self.project,
            title="Taxed invoice",
            tax_rate=Decimal(rate),
            due_date=timezone.localdate() + timedelta(days=30),
            created_by=self.admin,
        )
        InvoiceLineItem.objects.create(
            invoice=invoice,
            cost_code=self.code,
            description="Test service",
            quantity=1,
            unit_price=Decimal(price),
        )
        return issue_invoice(invoice_id=invoice.pk, actor=self.admin)

    def response(self, invoice, *, total=None, tax=None):
        return {
            "Id": "99",
            "SyncToken": "0",
            "CustomerRef": {"value": "42"},
            "TotalAmt": str(invoice.total_amount if total is None else total),
            "Balance": str(invoice.total_amount if total is None else total),
            "TxnTaxDetail": {
                "TxnTaxCodeRef": {"value": "2"},
                "TotalTax": str(invoice.tax_amount if tax is None else tax),
            },
        }

    def sync(self, invoice):
        return start_invoice_sync(
            invoice_id=invoice.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=self.api,
        )

    def test_taxed_invoice_uses_explicit_code_and_reconciles(self):
        mapping = self.map_rate()
        invoice = self.invoice()
        self.api.create_invoice.return_value = self.response(invoice)
        attempt = self.sync(invoice)
        self.assertEqual(attempt.status, "succeeded")
        payload = self.api.create_invoice.call_args.args[1]
        self.assertEqual(payload["TxnTaxDetail"], {"TxnTaxCodeRef": {"value": "2"}})
        self.assertEqual(
            payload["Line"][0]["SalesItemLineDetail"]["TaxCodeRef"], {"value": "TAX"}
        )
        self.assertNotIn("GlobalTaxCalculation", payload)
        self.assertEqual(attempt.response_snapshot["TxnTaxDetail"]["TotalTax"], "8.00")
        self.assertEqual(mapping.last_verified_values["rate"], "8")
        self.assertEqual(
            ActivityEvent.objects.filter(event_type="quickbooks_tax_mapped").count(), 1
        )

    def test_zero_rate_explicitly_marks_us_lines_nontaxable(self):
        invoice = self.invoice(rate="0")
        self.api.create_invoice.return_value = self.response(invoice)
        self.assertEqual(self.sync(invoice).status, "succeeded")
        payload = self.api.create_invoice.call_args.args[1]
        self.assertEqual(
            payload["Line"][0]["SalesItemLineDetail"]["TaxCodeRef"], {"value": "NON"}
        )
        self.assertNotIn("TxnTaxDetail", payload)
        self.api.get_tax_code.assert_not_called()

    def test_json_float_amounts_preserve_decimal_currency(self):
        self.api.get_tax_rate.return_value["RateValue"] = 9.1
        save_tax_mapping(
            connection=self.connection,
            local_tax_rate=Decimal("9.1"),
            code_id="2",
            actor=self.admin,
            api_client=self.api,
        )
        invoice = self.invoice(rate="9.1")
        response = self.response(invoice)
        response.update(TotalAmt=109.1, Balance=109.1)
        response["TxnTaxDetail"]["TotalTax"] = 9.1
        self.api.create_invoice.return_value = response
        attempt = self.sync(invoice)
        self.assertEqual(attempt.status, "succeeded")
        self.assertEqual(
            attempt.invoice_mapping.external_total_amount, Decimal("109.10")
        )
        self.assertEqual(attempt.invoice_mapping.external_balance, Decimal("109.10"))

    def test_invoice_mapping_rejects_invalid_money_without_rounding(self):
        invoice = self.invoice()
        for value in ["NaN", "Infinity", "-1", "1.001", "1000000000000", "bad"]:
            with self.subTest(value=value):
                with self.assertRaisesMessage(
                    QuickBooksMappingError, "invalid invoice amount"
                ):
                    save_invoice_mapping(
                        invoice=invoice,
                        connection=self.connection,
                        quickbooks_invoice=self.response(invoice, total=value),
                        actor=self.admin,
                    )
                self.assertFalse(
                    self.connection.invoice_mappings.filter(invoice=invoice).exists()
                )

    def test_nonzero_rate_rounding_to_zero_still_requires_mapping(self):
        invoice = self.invoice(price=".01")
        self.assertEqual(invoice.tax_amount, Decimal("0"))
        with self.assertRaisesMessage(
            QuickBooksSyncError, "verified sales tax mapping"
        ):
            self.sync(invoice)
        self.api.create_invoice.assert_not_called()

    def test_percentage_mismatch_does_not_save(self):
        with self.assertRaisesMessage(QuickBooksMappingError, "exactly match"):
            save_tax_mapping(
                connection=self.connection,
                local_tax_rate=Decimal("7.25"),
                code_id="2",
                actor=self.admin,
                api_client=self.api,
            )
        self.assertFalse(QuickBooksTaxCodeMapping.objects.exists())

    def test_additive_group_is_supported_but_compound_is_rejected(self):
        self.tax_code["SalesTaxRateList"]["TaxRateDetail"].append(
            {"TaxRateRef": {"value": "4"}, "TaxTypeApplicable": "TaxOnAmount"}
        )
        self.api.get_tax_rate.side_effect = lambda conn, id: {
            "Id": id,
            "Active": True,
            "RateValue": "6" if id == "3" else "2",
        }
        self.map_rate()
        self.tax_code["SalesTaxRateList"]["TaxRateDetail"][1]["TaxTypeApplicable"] = (
            "TaxOnTax"
        )
        with self.assertRaisesMessage(QuickBooksMappingError, "Compound"):
            self.map_rate()

    def test_inactive_and_invalid_rates_are_rejected(self):
        for values in (
            {"Active": False},
            {"RateValue": "NaN"},
            {"RateValue": "-1"},
            {"RateValue": "Infinity"},
            {"EffectiveTaxRate": [{"RateValue": 8}]},
        ):
            with self.subTest(values=values):
                self.api.get_tax_rate.return_value = {
                    "Id": "3",
                    "Active": True,
                    "RateValue": "8",
                    **values,
                }
                with self.assertRaises(QuickBooksMappingError):
                    self.map_rate()
        self.assertFalse(QuickBooksTaxCodeMapping.objects.exists())

    def test_unsupported_company_tax_modes_stop_before_write(self):
        self.map_rate()
        invoice = self.invoice()
        for preferences in (
            {"TaxPrefs": {"UsingSalesTax": False}},
            {"TaxPrefs": {"UsingSalesTax": True, "PartnerTaxEnabled": True}},
            {
                "TaxPrefs": {"UsingSalesTax": True},
                "CurrencyPrefs": {"MultiCurrencyEnabled": True},
            },
        ):
            with self.subTest(preferences=preferences):
                self.api.get_preferences.return_value = preferences
                attempt = self.sync(invoice)
                self.assertEqual(attempt.status, "failed")
                self.assertFalse(attempt.retryable)
        self.api.create_invoice.assert_not_called()

    def test_country_and_changed_rate_are_rechecked(self):
        self.map_rate()
        invoice = self.invoice()
        self.connection.country = "CA"
        self.connection.save(update_fields=("country",))
        self.assertIn("US QuickBooks", self.sync(invoice).error_message)
        self.connection.country = "US"
        self.connection.save(update_fields=("country",))
        self.api.get_tax_rate.return_value["RateValue"] = "9"
        attempt = self.sync(invoice)
        self.assertIn("rate changed", attempt.error_message)
        self.assertFalse(attempt.retryable)
        self.api.create_invoice.assert_not_called()

    def test_tax_mismatch_fails_even_if_grand_total_matches(self):
        self.map_rate()
        invoice = self.invoice()
        self.api.create_invoice.return_value = self.response(invoice, tax="0")
        attempt = self.sync(invoice)
        self.assertEqual(attempt.status, "failed")
        self.assertFalse(attempt.retryable)
        self.assertEqual(attempt.invoice_mapping.quickbooks_invoice_id, "99")
        self.assertIn("sales tax", attempt.error_message)

    def test_retry_reuses_payload_and_request_id(self):
        self.map_rate()
        invoice = self.invoice()
        self.api.create_invoice.side_effect = QuickBooksAPIError(
            "api_unavailable", "Timeout", retryable=True
        )
        failed = self.sync(invoice)
        self.api.create_invoice.side_effect = None
        self.api.create_invoice.return_value = self.response(invoice)
        retried = retry_invoice_sync_attempt(
            failed.pk, actor=self.admin, api_client=self.api
        )
        self.assertEqual(retried.status, "succeeded")
        self.assertEqual(failed.request_id, retried.request_id)
        self.assertEqual(failed.request_payload, retried.request_payload)

    def test_queued_payload_cannot_silently_switch_jurisdiction(self):
        mapping = self.map_rate()
        invoice = self.invoice()
        self.api.create_invoice.side_effect = QuickBooksAPIError(
            "api_unavailable", "Timeout", retryable=True
        )
        failed = self.sync(invoice)
        mapping.quickbooks_tax_code_id = "different"
        mapping.save()
        self.api.create_invoice.reset_mock()
        retried = retry_invoice_sync_attempt(
            failed.pk, actor=self.admin, api_client=self.api
        )
        self.assertEqual(retried.status, "failed")
        self.assertFalse(retried.retryable)
        self.api.create_invoice.assert_not_called()

    def test_unlink_is_audited_and_blocks_new_exports(self):
        mapping = self.map_rate()
        unlink_tax_mapping(mapping, actor=self.admin)
        mapping.refresh_from_db()
        self.assertFalse(mapping.is_active)
        self.assertTrue(
            ActivityEvent.objects.filter(event_type="quickbooks_tax_unlinked").exists()
        )
        with self.assertRaises(QuickBooksSyncError):
            self.sync(self.invoice())

    def test_unauthorized_users_cannot_read_or_change_tax_mappings(self):
        mapping = self.map_rate()
        other_org = Organization.objects.create(name="Other", slug="other-tax")
        outsider = get_user_model().objects.create_user(email="other-admin@example.com")
        OrganizationMembership.objects.create(
            organization=other_org, user=outsider, role="admin"
        )
        for user in (self.homeowner, outsider):
            with self.subTest(user=user.pk):
                self.client.force_login(user)
                url = reverse(
                    "integrations:quickbooks_tax_mappings", args=(self.connection.pk,)
                )
                with patch("integrations.tax_views.tax_code_options") as options:
                    self.assertEqual(self.client.get(url).status_code, 403)
                    self.assertEqual(
                        self.client.post(
                            url, {"local_tax_rate": "8", "tax_code": "2"}
                        ).status_code,
                        403,
                    )
                    options.assert_not_called()
                url = reverse(
                    "integrations:quickbooks_tax_mapping_unlink", args=(mapping.pk,)
                )
                self.assertEqual(self.client.post(url).status_code, 403)
                with self.assertRaises(PermissionDenied):
                    save_tax_mapping(
                        connection=self.connection,
                        local_tax_rate=Decimal("8"),
                        code_id="2",
                        actor=user,
                        api_client=self.api,
                    )

    @patch("integrations.taxes.QuickBooksAccountingClient")
    def test_admin_can_choose_save_and_remove_mapping(self, api_class):
        api_class.return_value = self.api
        self.client.force_login(self.admin)
        url = reverse(
            "integrations:quickbooks_tax_mappings", args=(self.connection.pk,)
        )
        response = self.client.get(url)
        self.assertContains(response, "Test jurisdiction")
        self.assertContains(response, "jurisdiction")
        response = self.client.post(url, {"local_tax_rate": "8", "tax_code": "2"})
        self.assertEqual(response.status_code, 302)
        mapping = QuickBooksTaxCodeMapping.objects.get()
        unlink_url = reverse(
            "integrations:quickbooks_tax_mapping_unlink", args=(mapping.pk,)
        )
        self.assertEqual(self.client.get(unlink_url).status_code, 405)
        self.assertEqual(self.client.post(unlink_url).status_code, 302)
        mapping.refresh_from_db()
        self.assertFalse(mapping.is_active)

    @patch("integrations.taxes.QuickBooksAccountingClient")
    def test_api_failure_is_visible_and_does_not_save(self, api_class):
        api_class.return_value = self.api
        self.api.get_preferences.side_effect = QuickBooksAPIError(
            "api_unavailable", "QuickBooks could not be reached."
        )
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("integrations:quickbooks_tax_mappings", args=(self.connection.pk,)),
            {"local_tax_rate": "8", "tax_code": "2"},
        )
        self.assertContains(response, "QuickBooks could not be reached.")
        self.assertFalse(QuickBooksTaxCodeMapping.objects.exists())

    def test_reserved_codes_are_not_offered(self):
        self.api.iter_tax_codes.return_value = [
            {"Id": "TAX", "Taxable": True},
            self.tax_code,
        ]
        self.assertEqual(
            [
                option["id"]
                for option in tax_code_options(self.connection, api_client=self.api)
            ],
            ["2"],
        )
