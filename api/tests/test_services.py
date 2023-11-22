"""Service layer: a token refreshed during a read must outlive that read."""

from __future__ import annotations

from django.test import SimpleTestCase, override_settings

from api.ledger import LedgerNotFound
from api.services import LedgerService
from api.tokens import InMemoryTokenStore

from .fixtures import QBO_BILL, qbo_query
from .support import FakeTransport, NoNetworkMixin, json_response, live_token

COMPANY = "4620816365361496080"

PROVIDERS = {
    "quickbooks": {
        "client_id": "qb-id",
        "client_secret": "qb-secret",
        "redirect_uri": "http://localhost:8870/accounts/quickbooks/login/callback/",
        "company_id": COMPANY,
    },
}

REFRESHED = json_response(
    {"access_token": "fresh-access", "refresh_token": "rotated-refresh", "expires_in": 3600}
)


@override_settings(LEDGER_PROVIDERS=PROVIDERS)
class RefreshPersistenceTests(NoNetworkMixin, SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.store = InMemoryTokenStore()
        self.transport = FakeTransport({"tokens/bearer": REFRESHED})
        self.service = LedgerService(self.store, transport=self.transport)

    def _store_expired_token(self):
        self.store.set("quickbooks", live_token(expires_at=1.0, tenant_id=COMPANY))

    def test_refreshed_token_is_written_back_to_the_store(self):
        self._store_expired_token()
        self.transport.route("/query", json_response(qbo_query("Bill", [QBO_BILL])))

        self.service.list_bills("quickbooks", limit=5)

        stored = self.store.get("quickbooks")
        self.assertEqual(stored.access_token, "fresh-access")
        self.assertEqual(stored.refresh_token, "rotated-refresh")

    def test_second_read_does_not_refresh_again(self):
        self._store_expired_token()
        self.transport.route("/query", json_response(qbo_query("Bill", [QBO_BILL])))

        self.service.list_bills("quickbooks")
        self.service.list_bills("quickbooks")

        refreshes = [r for r in self.transport.requests if "tokens/bearer" in r.url]
        self.assertEqual(len(refreshes), 1)

    def test_refresh_is_kept_even_when_the_read_fails(self):
        """A rotated refresh token is the only valid one left; losing it on a 404
        would disconnect the ledger."""
        self._store_expired_token()
        self.transport.route("/bill/", json_response({"Fault": {}}, status_code=404))

        with self.assertRaises(LedgerNotFound):
            self.service.get_bill("quickbooks", "999")

        self.assertEqual(self.store.get("quickbooks").refresh_token, "rotated-refresh")

    def test_unexpired_token_is_not_rewritten(self):
        token = live_token(tenant_id=COMPANY)
        self.store.set("quickbooks", token)
        self.transport.route("/query", json_response(qbo_query("Bill", [])))

        self.service.list_bills("quickbooks")

        self.assertIs(self.store.get("quickbooks"), token)
