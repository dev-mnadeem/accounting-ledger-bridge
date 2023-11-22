"""The shared OAuth 2.0 authorization-code flow."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from django.test import SimpleTestCase

from api.ledger import (
    LedgerAuthError,
    LedgerConfigurationError,
    OAuth2Credentials,
    QuickBooksLedgerClient,
    TokenSet,
)
from api.ledger.oauth import EXPIRY_SKEW_SECONDS, OAuth2Config, OAuth2Flow

from .support import CREDENTIALS, FakeTransport, NoNetworkMixin, json_response

CONFIG = OAuth2Config(
    authorize_url="https://example.test/authorize",
    token_url="https://example.test/token",
    scopes=("scope.a", "scope.b"),
)


def _flow(transport=None, credentials=CREDENTIALS) -> OAuth2Flow:
    return OAuth2Flow(
        config=CONFIG,
        credentials=credentials,
        transport=transport or FakeTransport(),
        provider="testledger",
    )


class AuthorizationUrlTests(NoNetworkMixin, SimpleTestCase):
    def test_contains_every_required_parameter(self):
        url, state = _flow().authorization_url()
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
                         CONFIG.authorize_url)
        self.assertEqual(query["client_id"], ["test-client-id"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["scope"], ["scope.a scope.b"])
        self.assertEqual(query["redirect_uri"], [CREDENTIALS.redirect_uri])
        self.assertEqual(query["state"], [state])

    def test_state_is_random_per_call(self):
        first = _flow().authorization_url()[1]
        second = _flow().authorization_url()[1]
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 20)

    def test_caller_supplied_state_is_honoured(self):
        _, state = _flow().authorization_url("fixed-state")
        self.assertEqual(state, "fixed-state")

    def test_missing_credentials_are_all_named(self):
        blank = OAuth2Credentials(client_id="", client_secret="", redirect_uri="")
        with self.assertRaises(LedgerConfigurationError) as ctx:
            _flow(credentials=blank).authorization_url()
        message = str(ctx.exception)
        for field in ("client_id", "client_secret", "redirect_uri"):
            self.assertIn(field, message)


class TokenExchangeTests(NoNetworkMixin, SimpleTestCase):
    def test_exchange_posts_basic_auth_and_form_body(self):
        transport = FakeTransport({
            "/token": json_response(
                {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}
            )
        })
        token = _flow(transport).exchange_code("the-code")

        self.assertEqual(token.access_token, "AT")
        self.assertEqual(token.refresh_token, "RT")
        sent = transport.last
        self.assertEqual(sent.method, "POST")
        self.assertEqual(sent.auth, ("test-client-id", "test-client-secret"))
        self.assertEqual(sent.data["grant_type"], "authorization_code")
        self.assertEqual(sent.data["code"], "the-code")
        self.assertEqual(sent.data["redirect_uri"], CREDENTIALS.redirect_uri)

    def test_empty_code_never_reaches_the_provider(self):
        transport = FakeTransport()
        with self.assertRaises(LedgerAuthError):
            _flow(transport).exchange_code("")
        self.assertEqual(transport.requests, [])

    def test_rejected_credentials_raise_auth_error(self):
        transport = FakeTransport({"/token": json_response({"error": "invalid"}, 401)})
        with self.assertRaises(LedgerAuthError):
            _flow(transport).exchange_code("code")

    def test_token_response_without_access_token_is_an_error(self):
        transport = FakeTransport({"/token": json_response({"expires_in": 3600})})
        with self.assertRaises(LedgerAuthError):
            _flow(transport).exchange_code("code")

    def test_non_json_token_response_is_an_error(self):
        transport = FakeTransport({"/token": json_response(None)})
        with self.assertRaises(LedgerAuthError):
            _flow(transport).exchange_code("code")


class RefreshTests(NoNetworkMixin, SimpleTestCase):
    def test_refresh_sends_the_refresh_grant(self):
        transport = FakeTransport({
            "/token": json_response(
                {"access_token": "AT2", "refresh_token": "RT2", "expires_in": 3600}
            )
        })
        refreshed = _flow(transport).refresh(TokenSet("old", "RT1", 0.0, "tenant-9"))
        self.assertEqual(refreshed.access_token, "AT2")
        self.assertEqual(refreshed.refresh_token, "RT2")
        self.assertEqual(refreshed.tenant_id, "tenant-9")
        self.assertEqual(transport.last.data["grant_type"], "refresh_token")
        self.assertEqual(transport.last.data["refresh_token"], "RT1")

    def test_rotation_without_a_new_refresh_token_keeps_the_old_one(self):
        transport = FakeTransport({
            "/token": json_response({"access_token": "AT2", "expires_in": 3600})
        })
        refreshed = _flow(transport).refresh(TokenSet("old", "RT1", 0.0))
        self.assertEqual(refreshed.refresh_token, "RT1")

    def test_refresh_without_a_refresh_token_is_refused_offline(self):
        transport = FakeTransport()
        with self.assertRaises(LedgerAuthError):
            _flow(transport).refresh(TokenSet("old", ""))
        self.assertEqual(transport.requests, [])


class TokenSetTests(SimpleTestCase):
    def test_expiry_uses_a_safety_skew(self):
        token = TokenSet("AT", "RT", expires_at=1_000.0)
        self.assertFalse(token.is_expired(now=1_000 - EXPIRY_SKEW_SECONDS - 1))
        self.assertTrue(token.is_expired(now=1_000 - EXPIRY_SKEW_SECONDS))
        self.assertTrue(token.is_expired(now=1_001))

    def test_token_without_an_expiry_never_expires(self):
        self.assertFalse(TokenSet("AT", "RT", expires_at=0.0).is_expired(now=10**12))

    def test_round_trips_through_a_session_payload(self):
        token = TokenSet("AT", "RT", 123.0, "tenant")
        self.assertEqual(TokenSet.from_dict(token.as_dict()), token)

    def test_from_payload_computes_absolute_expiry(self):
        token = TokenSet.from_payload({"access_token": "AT", "expires_in": 3600}, now=1_000.0)
        self.assertEqual(token.expires_at, 4_600.0)


class ExpiredTokenTests(NoNetworkMixin, SimpleTestCase):
    def test_accessing_an_expired_token_refreshes_it(self):
        transport = FakeTransport({
            "/tokens/bearer": json_response(
                {"access_token": "fresh", "refresh_token": "RT2", "expires_in": 3600}
            )
        })
        client = QuickBooksLedgerClient(
            CREDENTIALS, transport=transport,
            token=TokenSet("stale", "RT1", expires_at=1.0), company_id="4620816365361496080",
        )
        self.assertEqual(client.token.access_token, "fresh")

    def test_unconnected_client_raises_instead_of_calling_out(self):
        transport = FakeTransport()
        client = QuickBooksLedgerClient(CREDENTIALS, transport=transport)
        with self.assertRaises(LedgerAuthError):
            _ = client.token
        self.assertEqual(transport.requests, [])
