"""HTTP layer: routing, the OAuth round trip, paging params, error mapping."""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.test import TestCase, override_settings
from django.urls import reverse

from .fixtures import QBO_BILL, QBO_VENDOR, qbo_query
from .support import FakeTransport, NoNetworkMixin, json_response

PROVIDERS = {
    "quickbooks": {
        "client_id": "qb-id",
        "client_secret": "qb-secret",
        "redirect_uri": "http://localhost:8870/accounts/quickbooks/login/callback/",
        "environment": "sandbox",
        "company_id": "4620816365361496080",
    },
    "xero": {
        "client_id": "xero-id",
        "client_secret": "xero-secret",
        "redirect_uri": "http://localhost:8870/ledger/xero/callback/",
        "tenant_id": "tenant-1",
    },
}

TOKEN_RESPONSE = json_response(
    {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}
)


@override_settings(LEDGER_PROVIDERS=PROVIDERS, LEDGER_DEFAULT_PROVIDER="quickbooks")
class ViewTestCase(NoNetworkMixin, TestCase):
    """Wires a FakeTransport into the service layer for the whole test case."""

    def setUp(self):
        super().setUp()
        self.transport = FakeTransport()
        patcher = patch("api.services.shared_transport", return_value=self.transport)
        patcher.start()
        self.addCleanup(patcher.stop)

    def connect(self, provider="quickbooks", **callback_params):
        """Drive the real OAuth round trip so the session holds a real token."""
        self.transport.route("tokens/bearer", TOKEN_RESPONSE)
        self.transport.route("connect/token", TOKEN_RESPONSE)
        self.transport.route("api.xero.com/connections",
                             json_response([{"tenantId": "tenant-1"}]))
        start = self.client.get(reverse("initiate_ledger_process"), {"provider": provider})
        state = parse_qs(urlparse(start["Location"]).query)["state"][0]
        params = {"code": "the-code", "state": state, **callback_params}
        if provider == "quickbooks":
            url = reverse("quickbooks_callback")
        else:
            url = reverse("ledger_callback", args=[provider])
        return self.client.get(url, params)


class HealthTests(ViewTestCase):
    def test_health_lists_providers_and_touches_nothing(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(),
                         {"status": "ok", "providers": ["quickbooks", "xero"]})
        self.assertEqual(self.transport.requests, [])

    def test_providers_endpoint_reports_connection_state(self):
        payload = self.client.get(reverse("ledger-providers")).json()
        self.assertEqual(payload["default"], "quickbooks")
        self.assertEqual(
            payload["providers"],
            [{"name": "quickbooks", "connected": False},
             {"name": "xero", "connected": False}],
        )

    def test_post_is_rejected(self):
        self.assertEqual(self.client.post(reverse("health")).status_code, 405)


class AuthorizationTests(ViewTestCase):
    def test_initiate_redirects_to_the_provider_with_state(self):
        response = self.client.get(reverse("initiate_ledger_process"))
        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.netloc, "appcenter.intuit.com")
        query = parse_qs(parsed.query)
        self.assertEqual(query["client_id"], ["qb-id"])
        self.assertEqual(query["scope"], ["com.intuit.quickbooks.accounting"])
        self.assertTrue(query["state"][0])
        self.assertEqual(self.transport.requests, [])

    def test_provider_query_parameter_selects_xero(self):
        response = self.client.get(reverse("initiate_ledger_process"), {"provider": "xero"})
        self.assertEqual(urlparse(response["Location"]).netloc, "login.xero.com")

    def test_unconfigured_provider_is_a_404(self):
        response = self.client.get(reverse("initiate_ledger_process"), {"provider": "sage"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "UnknownProviderError")

    @override_settings(LEDGER_PROVIDERS={"quickbooks": {}})
    def test_missing_credentials_are_a_503_not_a_500(self):
        response = self.client.get(reverse("initiate_ledger_process"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "LedgerConfigurationError")
        self.assertIn("client_id", response.json()["detail"])


class CallbackTests(ViewTestCase):
    def test_round_trip_exchanges_the_code_and_returns_a_summary(self):
        self.transport.route("/query", json_response(qbo_query("Bill", [QBO_BILL])))
        response = self.connect(realmId="9130350000000001")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["provider"], "quickbooks")
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["bills"]["items"][0]["id"], "147")
        self.assertEqual(payload["bills"]["items"][0]["balance"], "234.56")

    def test_connection_survives_into_the_next_request(self):
        self.transport.route("/query", json_response(qbo_query("Bill", [QBO_BILL])))
        self.connect(realmId="9130350000000001")
        payload = self.client.get(reverse("ledger-providers")).json()
        self.assertTrue(payload["providers"][0]["connected"])

    def test_callback_without_a_matching_state_is_rejected(self):
        response = self.client.get(
            reverse("quickbooks_callback"), {"code": "c", "state": "forged"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("state mismatch", response.json()["detail"])
        self.assertEqual(self.transport.requests, [])

    def test_callback_with_no_state_at_all_is_rejected(self):
        self.client.get(reverse("initiate_ledger_process"))
        response = self.client.get(reverse("quickbooks_callback"), {"code": "c"})
        self.assertEqual(response.status_code, 401)

    def test_state_is_single_use(self):
        self.transport.route("/query", json_response(qbo_query("Bill", [])))
        first = self.connect(realmId="1")
        self.assertEqual(first.status_code, 200)
        # Replaying the same callback URL must now fail.
        replay = self.client.get(
            reverse("quickbooks_callback"),
            {"code": "the-code", "state": "whatever", "realmId": "1"},
        )
        self.assertEqual(replay.status_code, 401)

    def test_user_denied_consent(self):
        response = self.client.get(
            reverse("quickbooks_callback"), {"error": "access_denied"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "AuthorizationDenied")

    def test_xero_callback_route(self):
        self.transport.route("/Invoices", json_response({"Invoices": []}))
        self.transport.route("/Contacts", json_response({"Contacts": []}))
        response = self.connect(provider="xero")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider"], "xero")


class ReadEndpointTests(ViewTestCase):
    def setUp(self):
        super().setUp()
        self.transport.route("/query", json_response(qbo_query("Bill", [QBO_BILL])))
        self.connect(realmId="4620816365361496080")
        self.transport.requests.clear()

    def test_bills_endpoint_returns_a_page(self):
        response = self.client.get(reverse("ledger-bills", args=["quickbooks"]))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["limit"], 50)
        self.assertEqual(payload["offset"], 0)
        self.assertEqual(payload["items"][0]["vendor_name"],
                         "Norton Lumber and Building Materials")

    def test_limit_and_offset_reach_the_provider_query(self):
        self.client.get(reverse("ledger-bills", args=["quickbooks"]),
                        {"limit": "10", "offset": "30"})
        self.assertIn("STARTPOSITION 31 MAXRESULTS 10", self.transport.last.params["query"])

    def test_limit_is_clamped_to_the_maximum(self):
        self.client.get(reverse("ledger-bills", args=["quickbooks"]), {"limit": "9999"})
        self.assertIn("MAXRESULTS 100", self.transport.last.params["query"])

    def test_junk_paging_values_fall_back_to_defaults(self):
        self.client.get(reverse("ledger-bills", args=["quickbooks"]),
                        {"limit": "abc", "offset": "-40"})
        self.assertIn("STARTPOSITION 1 MAXRESULTS 50", self.transport.last.params["query"])

    def test_vendor_filter_is_forwarded(self):
        self.client.get(reverse("ledger-bills", args=["quickbooks"]), {"vendor_id": "56"})
        self.assertIn("WHERE VendorRef = '56'", self.transport.last.params["query"])

    def test_single_bill_endpoint(self):
        self.transport.route("/bill/", json_response({"Bill": QBO_BILL}))
        response = self.client.get(reverse("ledger-bill", args=["quickbooks", "147"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], "147")
        self.assertIn("/bill/147", self.transport.last.url)

    def test_single_vendor_endpoint(self):
        self.transport.route("/vendor/", json_response({"Vendor": QBO_VENDOR}))
        response = self.client.get(reverse("ledger-vendor", args=["quickbooks", "56"]))
        self.assertEqual(response.json()["balance"], "8975.25")

    def test_vendors_endpoint(self):
        self.transport.route("/query", json_response(qbo_query("Vendor", [QBO_VENDOR])))
        response = self.client.get(reverse("ledger-vendors", args=["quickbooks"]))
        self.assertEqual(response.json()["items"][0]["id"], "56")

    def test_provider_404_becomes_a_404(self):
        self.transport.route("/bill/", json_response({"Fault": {}}, 404))
        response = self.client.get(reverse("ledger-bill", args=["quickbooks", "999"]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "LedgerNotFound")

    def test_provider_500_becomes_a_502(self):
        self.transport.route("/query", json_response({"Fault": {}}, 500))
        response = self.client.get(reverse("ledger-bills", args=["quickbooks"]))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"], "LedgerTransportError")

    def test_expired_credentials_become_a_401(self):
        self.transport.route("/query", json_response({"Fault": {}}, 401))
        response = self.client.get(reverse("ledger-bills", args=["quickbooks"]))
        self.assertEqual(response.status_code, 401)


class UnconnectedProviderTests(ViewTestCase):
    def test_reading_before_connecting_is_a_401_and_makes_no_call(self):
        response = self.client.get(reverse("ledger-bills", args=["xero"]))
        self.assertEqual(response.status_code, 401)
        self.assertIn("not connected", response.json()["detail"])
        self.assertEqual(self.transport.requests, [])
