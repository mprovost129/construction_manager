from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse

from integrations.customer_sync import QuickBooksSyncError
from integrations.item_accounts import income_account_options
from integrations.item_sync import retry_item_sync_attempt, start_cost_code_item_sync
from integrations.models import QuickBooksConnection, QuickBooksItemMapping
from integrations.quickbooks import QuickBooksAccountingClient, QuickBooksAPIError
from integrations.tests import test_item_sync as fixtures
from projects.models import Organization


@override_settings(**fixtures.SYNC_SETTINGS)
class QuickBooksIncomeAccountTests(TestCase):
    setUp = fixtures.QuickBooksItemSyncTests.setUp

    def api(self):
        api = Mock()
        api.find_items_by_name.return_value = []
        api.create_item.return_value = fixtures.item()
        api.get_account.return_value = fixtures.income_account()
        api.iter_income_accounts.return_value = [fixtures.income_account()]
        return api

    def sync(self, api, account="79"):
        return start_cost_code_item_sync(
            cost_code_id=self.cost_code.pk,
            connection_id=self.connection.pk,
            actor=self.admin,
            api_client=api,
            income_account_id=account,
        )

    def url(self):
        return reverse(
            "integrations:quickbooks_service_item_setup", args=(self.cost_code.pk,)
        )

    def test_account_is_sent_and_preserved_in_mapping_snapshot(self):
        api = self.api()
        attempt = self.sync(api)
        self.assertEqual(attempt.status, "succeeded")
        self.assertEqual(
            api.create_item.call_args.args[1]["IncomeAccountRef"], {"value": "79"}
        )
        self.assertEqual(
            attempt.item_mapping.last_synced_values["IncomeAccountRef"]["value"], "79"
        )
        self.assertEqual(attempt.response_snapshot["IncomeAccountRef"]["value"], "79")

    def test_missing_account_blocks_legacy_create_before_api_write(self):
        api = self.api()
        attempt = self.sync(api, account=None)
        self.assertEqual(attempt.status, "failed")
        self.assertIn("Choose an income account", attempt.error_message)
        self.assertIsNone(attempt.dispatched_at)
        api.create_item.assert_not_called()

    def test_invalid_account_is_rechecked_before_write(self):
        api = self.api()
        for changes in (
            {"Active": False},
            {"AccountType": "Expense"},
            {"AccountSubType": "UnappliedCashPaymentIncome"},
            {"Id": "wrong"},
        ):
            with self.subTest(changes=changes):
                api.get_account.return_value = {**fixtures.income_account(), **changes}
                self.assertEqual(self.sync(api).status, "failed")
        api.create_item.assert_not_called()

    def test_retry_revalidates_account_and_keeps_original_payload(self):
        api = self.api()
        api.create_item.side_effect = QuickBooksAPIError(
            "api_unavailable", "Timeout", retryable=True
        )
        failed = self.sync(api)
        original = api.create_item.call_args
        with self.assertRaises(QuickBooksSyncError):
            self.sync(api, account="other")
        api.create_item.side_effect = None
        result = retry_item_sync_attempt(failed.pk, actor=self.admin, api_client=api)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(api.create_item.call_args, original)

    def test_account_deactivated_before_retry_stops_write(self):
        api = self.api()
        api.create_item.side_effect = QuickBooksAPIError(
            "api_unavailable", "Timeout", retryable=True
        )
        failed = self.sync(api)
        api.get_account.return_value["Active"] = False
        self.assertEqual(
            retry_item_sync_attempt(failed.pk, actor=self.admin, api_client=api).status,
            "failed",
        )
        self.assertEqual(api.create_item.call_count, 1)

    def test_existing_name_with_wrong_account_or_type_requires_review(self):
        api = self.api()
        for matches in (
            [{**fixtures.item(), "IncomeAccountRef": {"value": "other"}}],
            [{**fixtures.item(), "Type": "Inventory"}],
            [{**fixtures.item(), "Active": False}],
            [fixtures.item(), fixtures.item(item_id="8")],
        ):
            with self.subTest(matches=matches):
                api.find_items_by_name.return_value = matches
                self.assertEqual(self.sync(api).status, "failed")
        api.create_item.assert_not_called()
        self.assertFalse(QuickBooksItemMapping.objects.exists())

    def test_existing_compatible_service_is_linked_without_edit(self):
        api = self.api()
        api.find_items_by_name.return_value = [fixtures.item()]
        self.assertEqual(self.sync(api).status, "succeeded")
        api.create_item.assert_not_called()
        api.update_item.assert_not_called()

    def test_returned_account_mismatch_preserves_remote_identity(self):
        api = self.api()
        api.create_item.return_value["IncomeAccountRef"] = {"value": "different"}
        attempt = self.sync(api)
        self.assertEqual(attempt.status, "failed")
        self.assertEqual(attempt.external_id, "7")
        self.assertFalse(QuickBooksItemMapping.objects.exists())
        with self.assertRaises(QuickBooksSyncError):
            self.sync(api)
        self.assertEqual(api.create_item.call_count, 1)

    def test_service_rejects_non_admin(self):
        api = self.api()
        with self.assertRaises(PermissionDenied):
            start_cost_code_item_sync(
                cost_code_id=self.cost_code.pk,
                connection_id=self.connection.pk,
                actor=self.staff,
                income_account_id="79",
                api_client=api,
            )
        api.find_items_by_name.assert_not_called()

    def test_stale_setup_form_cannot_change_existing_mapping_account(self):
        api = self.api()
        self.assertEqual(self.sync(api).status, 'succeeded')
        with self.assertRaisesMessage(QuickBooksSyncError, 'already mapped'):
            self.sync(api, account='different')
        self.assertEqual(api.create_item.call_count, 1)

    @patch("integrations.item_views.QuickBooksAccountingClient")
    def test_admin_page_requires_explicit_selection_then_creates(self, client):
        api = client.return_value = self.api()
        self.client.force_login(self.admin)
        response = self.client.get(self.url(), {"connection": self.connection.pk})
        self.assertContains(response, "Choose an income account")
        api.create_item.assert_not_called()
        response = self.client.post(
            self.url(), {"connection": self.connection.pk, "income_account": "79"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(api.create_item.call_count, 1)

    @patch("integrations.item_views.QuickBooksAccountingClient")
    def test_tampered_or_missing_account_cannot_create(self, client):
        api = client.return_value = self.api()
        self.client.force_login(self.admin)
        for selected in ("", "not-listed"):
            response = self.client.post(
                self.url(),
                {"connection": self.connection.pk, "income_account": selected},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        api.create_item.assert_not_called()

    @patch("integrations.item_views.QuickBooksAccountingClient")
    def test_other_company_connection_and_staff_are_rejected_before_api(self, client):
        other = Organization.objects.create(name="Other", slug="other-income")
        remote = QuickBooksConnection.objects.create(
            organization=other, realm_id="other", environment="sandbox"
        )
        self.client.force_login(self.admin)
        for method in (self.client.get, self.client.post):
            self.assertEqual(
                method(self.url(), {"connection": remote.pk}).status_code, 404
            )
        self.client.force_login(self.staff)
        for method in (self.client.get, self.client.post):
            self.assertEqual(
                method(self.url(), {"connection": self.connection.pk}).status_code, 403
            )
        client.assert_not_called()

    @patch("integrations.item_views.QuickBooksAccountingClient")
    def test_account_list_failure_is_safe_and_writes_nothing(self, client):
        api = client.return_value = self.api()
        api.iter_income_accounts.side_effect = QuickBooksAPIError(
            "api_unavailable", "QuickBooks is temporarily unavailable.", retryable=True
        )
        self.client.force_login(self.admin)
        response = self.client.post(
            self.url(), {"connection": self.connection.pk, "income_account": "79"}
        )
        self.assertContains(response, "temporarily unavailable")
        api.create_item.assert_not_called()

    def test_options_exclude_inactive_expense_and_special_accounts(self):
        api = self.api()
        api.iter_income_accounts.return_value = [
            fixtures.income_account(),
            {**fixtures.income_account("80"), "AccountType": "Expense"},
            {**fixtures.income_account("81"), "Active": False},
            {
                **fixtures.income_account("82"),
                "AccountSubType": "UnappliedCashPaymentIncome",
            },
        ]
        self.assertEqual(
            income_account_options(self.connection, api),
            [{"value": "79", "name": "Sales"}],
        )

    @patch("integrations.quickbooks.requests.get")
    def test_account_query_paginates_and_filters(self, get):
        get.side_effect = [
            Mock(
                status_code=200,
                json=Mock(
                    return_value={
                        "QueryResponse": {"Account": [fixtures.income_account()]}
                    }
                ),
            ),
            Mock(status_code=200, json=Mock(return_value={"QueryResponse": {}})),
        ]
        rows = list(
            QuickBooksAccountingClient().iter_income_accounts(
                self.connection, page_size=1
            )
        )
        self.assertEqual(len(rows), 1)
        self.assertIn(
            "AccountType = 'Income'", get.call_args_list[0].kwargs["params"]["query"]
        )
        self.assertIn(
            "STARTPOSITION 2", get.call_args_list[1].kwargs["params"]["query"]
        )

    @patch("integrations.quickbooks.requests.get")
    def test_malformed_account_query_is_rejected(self, get):
        get.return_value = Mock(
            status_code=200,
            json=Mock(return_value={"QueryResponse": {"Account": "bad"}}),
        )
        with self.assertRaises(QuickBooksAPIError):
            list(QuickBooksAccountingClient().iter_income_accounts(self.connection))
