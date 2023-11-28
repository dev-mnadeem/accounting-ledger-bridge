from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from api.ledger import TokenSet
from api.services import LedgerService
from api.tokens import InMemoryTokenStore

from .support import FakeTransport, NoNetworkMixin
from .test_views import PROVIDERS


@override_settings(LEDGER_PROVIDERS=PROVIDERS)
class RefreshPersistenceTests(NoNetworkMixin, SimpleTestCase):
    def test_next_request_uses_rotated_refresh_token_even_after_a_failed_read(self):
        store = InMemoryTokenStore()
        store.set("quickbooks", TokenSet("expired", "old-refresh", expires_at=1))
        service = LedgerService(store, transport=FakeTransport())
        client = service.client("quickbooks")
        rotated = TokenSet("replacement", "new-refresh", expires_at=9999999999)
        with patch.object(client._flow, "refresh", return_value=rotated):
            # The provider read fails after headers triggered the refresh.
            with self.assertRaisesRegex(AssertionError, "unrouted request"):
                client.get_bills()
        next_client = service.client("quickbooks")
        self.assertEqual(next_client.token.refresh_token, "new-refresh")
        self.assertEqual(next_client.token.access_token, "replacement")

    def test_persistence_failure_prevents_using_an_unstored_rotated_token(self):
        store = InMemoryTokenStore()
        store.set("quickbooks", TokenSet("expired", "old-refresh", expires_at=1))
        client = LedgerService(store, transport=FakeTransport()).client("quickbooks")
        rotated = TokenSet("replacement", "new-refresh", expires_at=9999999999)
        with patch.object(client._flow, "refresh", return_value=rotated), \
                patch.object(store, "set", side_effect=RuntimeError("storage unavailable")):
            with self.assertRaisesRegex(RuntimeError, "storage unavailable"):
                _ = client.token
        self.assertEqual(store.get("quickbooks").refresh_token, "old-refresh")
