"""Xero provider: the second implementation the abstract base was written for."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from api.ledger import (
    LedgerAuthError,
    LedgerNotFound,
    LedgerTransportError,
    XeroLedgerClient,
)

from .fixtures import XERO_CONTACT, XERO_INVOICE
from .support import CREDENTIALS, FakeTransport, NoNetworkMixin, json_response, live_token

TENANT = "0f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"

#: (num, offset) -> (page number Xero must be asked for, offset the Page reports).
#: Xero pages by 1-based page number, so an offset is floor-divided by the page
#: size and the resulting page's true offset is reported back.
PAGING_CASES = [
    ((50, 0), 1, 0),
    ((50, 50), 2, 50),
    ((50, 100), 3, 100),
    ((10, 0), 1, 0),
    ((10, 30), 4, 30),
    ((10, 35), 4, 30),   # mid-page offset snaps back to its page boundary
    ((25, 99), 4, 75),
]


def client(transport, **kwargs) -> XeroLedgerClient:
    kwargs.setdefault("tenant_id", TENANT)
    kwargs.setdefault("token", live_token(tenant_id=TENANT))
    return XeroLedgerClient(CREDENTIALS, transport=transport, **kwargs)


class ConstructionTests(NoNetworkMixin, SimpleTestCase):
    def test_constructing_a_client_makes_no_request(self):
        transport = FakeTransport()
        XeroLedgerClient(CREDENTIALS, transport=transport, tenant_id=TENANT)
        self.assertEqual(transport.requests, [])

    def test_missing_tenant_is_a_clear_error(self):
        transport = FakeTransport()
        naked = XeroLedgerClient(
            CREDENTIALS, transport=transport, token=live_token(tenant_id=""), tenant_id=""
        )
        with self.assertRaises(LedgerTransportError):
            naked.get_bills()
        self.assertEqual(transport.requests, [])


class QueryConstructionTests(NoNetworkMixin, SimpleTestCase):
    def test_only_payable_invoices_are_requested(self):
        transport = FakeTransport({"/Invoices": json_response({"Invoices": []})})
        client(transport).get_bills()
        self.assertIn('Type=="ACCPAY"', transport.last.params["where"])

    def test_vendor_filter_adds_a_contact_clause(self):
        transport = FakeTransport({"/Invoices": json_response({"Invoices": []})})
        client(transport).get_bills(50, "b1c2d3e4-0000-4000-8000-000000000001")
        where = transport.last.params["where"]
        self.assertIn('Type=="ACCPAY"', where)
        self.assertIn('Contact.ContactID==Guid("b1c2d3e4-0000-4000-8000-000000000001")', where)

    def test_tenant_header_is_sent(self):
        transport = FakeTransport({"/Invoices": json_response({"Invoices": []})})
        client(transport).get_bills()
        self.assertEqual(transport.last.headers["Xero-tenant-id"], TENANT)
        self.assertEqual(transport.last.headers["Authorization"], "Bearer test-access-token")

    def test_vendors_ask_only_for_suppliers(self):
        transport = FakeTransport({"/Contacts": json_response({"Contacts": []})})
        client(transport).get_vendors()
        self.assertEqual(transport.last.params["where"], "IsSupplier==true")

    def test_paging_table(self):
        for (num, offset), expected_page, expected_offset in PAGING_CASES:
            with self.subTest(num=num, offset=offset):
                transport = FakeTransport({"/Invoices": json_response({"Invoices": []})})
                page = client(transport).get_bills(num, offset=offset)
                self.assertEqual(transport.last.params["page"], expected_page)
                self.assertEqual(transport.last.params["pageSize"], min(num, 100))
                self.assertEqual(page.offset, expected_offset)


class MappingTests(NoNetworkMixin, SimpleTestCase):
    def test_invoice_maps_onto_the_shared_bill_type(self):
        transport = FakeTransport({"/Invoices": json_response({"Invoices": [XERO_INVOICE]})})
        bill = client(transport).get_bills().items[0]
        self.assertEqual(bill.provider, "xero")
        self.assertEqual(bill.id, "7c9e6679-7425-40de-944b-e07fc1f90ae7")
        self.assertEqual(bill.document_number, "INV-0042")
        self.assertEqual(bill.vendor_id, "b1c2d3e4-0000-4000-8000-000000000001")
        self.assertEqual(bill.vendor_name, "Ridgeway Supplies Ltd")
        self.assertEqual(bill.total, Decimal("4500.75"))
        self.assertEqual(bill.balance, Decimal("1500.25"))
        self.assertEqual(bill.currency, "GBP")
        self.assertEqual(bill.issued_on, date(2024, 5, 6))
        self.assertEqual(bill.due_on, date(2024, 6, 5))

    def test_contact_maps_onto_the_shared_vendor_type(self):
        transport = FakeTransport({"/Contacts": json_response({"Contacts": [XERO_CONTACT]})})
        vendor = client(transport).get_vendors().items[0]
        self.assertEqual(vendor.id, "b1c2d3e4-0000-4000-8000-000000000001")
        self.assertEqual(vendor.name, "Ridgeway Supplies Ltd")
        self.assertEqual(vendor.balance, Decimal("1500.25"))
        self.assertEqual(vendor.email, "accounts@ridgeway.example")
        self.assertTrue(vendor.active)

    def test_archived_contact_is_inactive(self):
        row = dict(XERO_CONTACT, ContactStatus="ARCHIVED")
        transport = FakeTransport({"/Contacts": json_response({"Contacts": [row]})})
        self.assertFalse(client(transport).get_vendors().items[0].active)

    def test_both_providers_produce_the_same_shape(self):
        transport = FakeTransport({"/Invoices": json_response({"Invoices": [XERO_INVOICE]})})
        payload = client(transport).get_bills().items[0].as_dict()
        self.assertEqual(
            sorted(payload),
            ["balance", "currency", "document_number", "due_on", "id", "is_paid",
             "issued_on", "provider", "total", "vendor_id", "vendor_name"],
        )


class SingleRecordTests(NoNetworkMixin, SimpleTestCase):
    def test_get_bill_unwraps_the_single_element_list(self):
        transport = FakeTransport({"/Invoices/": json_response({"Invoices": [XERO_INVOICE]})})
        bill = client(transport).get_bill("7c9e6679-7425-40de-944b-e07fc1f90ae7")
        self.assertEqual(bill.id, "7c9e6679-7425-40de-944b-e07fc1f90ae7")
        self.assertIn("/Invoices/7c9e6679-7425-40de-944b-e07fc1f90ae7", transport.last.url)

    def test_get_vendor_unwraps_the_single_element_list(self):
        transport = FakeTransport({"/Contacts/": json_response({"Contacts": [XERO_CONTACT]})})
        vendor = client(transport).get_vendor("b1c2d3e4-0000-4000-8000-000000000001")
        self.assertEqual(vendor.name, "Ridgeway Supplies Ltd")

    def test_empty_list_is_a_not_found(self):
        transport = FakeTransport({"/Invoices/": json_response({"Invoices": []})})
        with self.assertRaises(LedgerNotFound):
            client(transport).get_bill("missing")

    def test_blank_id_is_rejected_without_a_request(self):
        transport = FakeTransport()
        with self.assertRaises(LedgerNotFound):
            client(transport).get_bill("")
        with self.assertRaises(LedgerNotFound):
            client(transport).get_vendor("")
        self.assertEqual(transport.requests, [])


class TenantDiscoveryTests(NoNetworkMixin, SimpleTestCase):
    """Xero does not name the organisation on the callback; it must be read back."""

    def _connected_transport(self, connections):
        return FakeTransport({
            "identity.xero.com/connect/token": json_response(
                {"access_token": "AT", "refresh_token": "RT", "expires_in": 1800}
            ),
            "api.xero.com/connections": json_response(connections),
        })

    def test_first_connection_becomes_the_tenant(self):
        transport = self._connected_transport(
            [{"tenantId": TENANT, "tenantName": "Demo Co"}]
        )
        fresh = XeroLedgerClient(CREDENTIALS, transport=transport)
        token = fresh.connect("auth-code")
        self.assertEqual(token.tenant_id, TENANT)
        self.assertEqual(fresh.tenant_id, TENANT)
        self.assertEqual(
            transport.urls(),
            ["https://identity.xero.com/connect/token", "https://api.xero.com/connections"],
        )

    def test_explicit_tenant_skips_the_lookup(self):
        transport = self._connected_transport([])
        fresh = XeroLedgerClient(CREDENTIALS, transport=transport)
        fresh.connect("auth-code", tenant_id=TENANT)
        self.assertEqual(fresh.tenant_id, TENANT)
        self.assertEqual(len(transport.requests), 1)

    def test_no_connected_organisation_is_an_auth_error(self):
        transport = self._connected_transport([])
        fresh = XeroLedgerClient(CREDENTIALS, transport=transport)
        with self.assertRaises(LedgerAuthError):
            fresh.connect("auth-code")


class ErrorTranslationTests(NoNetworkMixin, SimpleTestCase):
    def test_401_becomes_auth_error(self):
        transport = FakeTransport({"/Invoices": json_response({}, 401)})
        with self.assertRaises(LedgerAuthError):
            client(transport).get_bills()

    def test_429_becomes_transport_error(self):
        transport = FakeTransport({"/Invoices": json_response({}, 429)})
        with self.assertRaises(LedgerTransportError) as ctx:
            client(transport).get_bills()
        self.assertEqual(ctx.exception.status_code, 429)
