"""The provider registry — the seam a third ledger would plug into."""

from __future__ import annotations

from django.test import SimpleTestCase

from api.ledger import (
    LedgerClientBase,
    QuickBooksLedgerClient,
    UnknownProviderError,
    XeroLedgerClient,
    available_providers,
    build_client,
    get_provider_class,
)
from api.ledger.base import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, clamp_page_size
from api.ledger.domain import Page
from api.ledger.registry import register

from .fixtures import qbo_query
from .support import FakeTransport, NoNetworkMixin, json_response, live_token

CLAMP_CASES = [
    (None, DEFAULT_PAGE_SIZE),
    (0, DEFAULT_PAGE_SIZE),
    (1, 1),
    (49, 49),
    (100, 100),
    (101, MAX_PAGE_SIZE),
    (10_000, MAX_PAGE_SIZE),
    (-1, 1),
]


class RegistryTests(SimpleTestCase):
    def test_both_shipped_providers_are_registered(self):
        self.assertEqual(available_providers(), ("quickbooks", "xero"))

    def test_lookup_returns_the_class(self):
        self.assertIs(get_provider_class("quickbooks"), QuickBooksLedgerClient)
        self.assertIs(get_provider_class("xero"), XeroLedgerClient)

    def test_unknown_provider_names_the_known_ones(self):
        with self.assertRaises(UnknownProviderError) as ctx:
            get_provider_class("sage")
        self.assertIn("quickbooks", str(ctx.exception))
        self.assertIn("xero", str(ctx.exception))

    def test_a_provider_must_declare_a_name(self):
        class Nameless(LedgerClientBase):
            pass

        with self.assertRaises(ValueError):
            register(Nameless)


class BuildClientTests(NoNetworkMixin, SimpleTestCase):
    CONFIG = {
        "client_id": "cid",
        "client_secret": "secret",
        "redirect_uri": "http://localhost:8870/cb/",
        "environment": "production",
        "company_id": "123456",
        # A key this provider does not accept must be ignored, not crash.
        "tenant_id": "not-for-quickbooks",
    }

    def test_extra_config_is_routed_to_the_matching_provider_only(self):
        client = build_client("quickbooks", self.CONFIG, transport=FakeTransport())
        self.assertIsInstance(client, QuickBooksLedgerClient)
        self.assertEqual(client.environment, "production")
        self.assertEqual(client.company_id, "123456")
        self.assertEqual(client.credentials.client_id, "cid")

        xero = build_client("xero", self.CONFIG, transport=FakeTransport())
        self.assertIsInstance(xero, XeroLedgerClient)
        self.assertEqual(xero.tenant_id, "not-for-quickbooks")

    def test_building_a_client_makes_no_request(self):
        transport = FakeTransport()
        build_client("quickbooks", self.CONFIG, transport=transport)
        build_client("xero", self.CONFIG, transport=transport)
        self.assertEqual(transport.requests, [])

    def test_missing_config_keys_default_to_blank_not_none(self):
        client = build_client("xero", {}, transport=FakeTransport())
        self.assertEqual(client.credentials.client_id, "")
        self.assertFalse(client.is_connected)

    def test_unknown_provider_is_refused(self):
        with self.assertRaises(UnknownProviderError):
            build_client("netsuite", {}, transport=FakeTransport())


class ContractConformanceTests(SimpleTestCase):
    """Every registered provider must honour the base contract identically."""

    def test_all_providers_implement_the_four_read_operations(self):
        for name in available_providers():
            client_cls = get_provider_class(name)
            with self.subTest(provider=name):
                self.assertFalse(getattr(client_cls, "__abstractmethods__", set()))
                for method in ("get_bills", "get_bill", "get_vendors", "get_vendor"):
                    self.assertTrue(callable(getattr(client_cls, method)))

    def test_collection_reads_return_a_page(self):
        for name in available_providers():
            client_cls = get_provider_class(name)
            with self.subTest(provider=name):
                self.assertEqual(
                    client_cls.get_bills.__annotations__.get("return"), "Page[Bill]"
                )
                self.assertEqual(
                    client_cls.get_vendors.__annotations__.get("return"), "Page[Vendor]"
                )
        self.assertTrue(issubclass(Page, object))


class ClampTests(SimpleTestCase):
    def test_table(self):
        for raw, expected in CLAMP_CASES:
            with self.subTest(raw=raw):
                self.assertEqual(clamp_page_size(raw), expected)


class EndpointOverrideTests(NoNetworkMixin, SimpleTestCase):
    """The documented dev/proxy seam: redirect a provider at another host."""

    BASE = {"client_id": "id", "client_secret": "s", "redirect_uri": "http://x/cb"}

    def test_quickbooks_api_base_override_is_used(self):
        transport = FakeTransport({"/query": json_response(qbo_query("Bill", []))})
        client = build_client(
            "quickbooks",
            {**self.BASE, "company_id": "1", "api_base": "http://127.0.0.1:8871/qbo/"},
            transport=transport,
            token=live_token(),
        )
        client.get_bills()
        self.assertTrue(
            transport.last.url.startswith("http://127.0.0.1:8871/qbo/v3/company/1/")
        )

    def test_quickbooks_token_url_override_is_used(self):
        transport = FakeTransport({
            "127.0.0.1:8871": json_response({"access_token": "AT", "expires_in": 60})
        })
        client = build_client(
            "quickbooks",
            {**self.BASE, "token_url": "http://127.0.0.1:8871/oauth/token"},
            transport=transport,
        )
        client.connect("code", realm_id="1")
        self.assertEqual(transport.last.url, "http://127.0.0.1:8871/oauth/token")

    def test_xero_api_base_override_also_moves_the_connections_url(self):
        transport = FakeTransport({
            "127.0.0.1:8871/oauth": json_response({"access_token": "AT", "expires_in": 60}),
            "127.0.0.1:8871/connections": json_response([{"tenantId": "T1"}]),
        })
        client = build_client(
            "xero",
            {
                **self.BASE,
                "api_base": "http://127.0.0.1:8871/api.xro/2.0",
                "token_url": "http://127.0.0.1:8871/oauth/token",
            },
            transport=transport,
        )
        client.connect("code")
        self.assertEqual(client.tenant_id, "T1")
        self.assertEqual(transport.urls()[-1], "http://127.0.0.1:8871/connections")

    def test_no_override_keeps_the_real_provider_hosts(self):
        qb = build_client("quickbooks", self.BASE, transport=FakeTransport())
        xero = build_client("xero", self.BASE, transport=FakeTransport())
        self.assertEqual(qb.api_base, "https://sandbox-quickbooks.api.intuit.com")
        self.assertEqual(xero.api_base, "https://api.xero.com/api.xro/2.0")
        self.assertEqual(xero.connections_url, "https://api.xero.com/connections")
