"""QuickBooks Online provider: query construction, mapping, paging, errors."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from api.ledger import (
    LedgerAuthError,
    LedgerNotFound,
    LedgerTransportError,
    QuickBooksLedgerClient,
)
from api.ledger.quickbooks import MINOR_VERSION

from .fixtures import QBO_BILL, QBO_BILL_PAID, QBO_VENDOR, qbo_query
from .support import CREDENTIALS, FakeTransport, NoNetworkMixin, json_response, live_token

COMPANY = "4620816365361496080"

#: (num, offset) -> the exact STARTPOSITION/MAXRESULTS the query must carry.
#: Derived by hand from the QuickBooks docs: STARTPOSITION is 1-based.
PAGING_CASES = [
    ((50, 0), "STARTPOSITION 1 MAXRESULTS 50"),
    ((50, 50), "STARTPOSITION 51 MAXRESULTS 50"),
    ((10, 0), "STARTPOSITION 1 MAXRESULTS 10"),
    ((10, 30), "STARTPOSITION 31 MAXRESULTS 10"),
    ((1, 7), "STARTPOSITION 8 MAXRESULTS 1"),
    ((None, 0), "STARTPOSITION 1 MAXRESULTS 50"),   # falsy -> default page
    ((0, 0), "STARTPOSITION 1 MAXRESULTS 50"),      # falsy -> default page
    ((500, 0), "STARTPOSITION 1 MAXRESULTS 100"),   # clamped to MAX_PAGE_SIZE
    ((-5, 0), "STARTPOSITION 1 MAXRESULTS 1"),      # clamped up to 1
]


def client(transport, **kwargs) -> QuickBooksLedgerClient:
    kwargs.setdefault("company_id", COMPANY)
    kwargs.setdefault("token", live_token())
    return QuickBooksLedgerClient(CREDENTIALS, transport=transport, **kwargs)


class ConstructionTests(NoNetworkMixin, SimpleTestCase):
    def test_constructing_a_client_makes_no_request(self):
        transport = FakeTransport()
        QuickBooksLedgerClient(CREDENTIALS, transport=transport, company_id=COMPANY)
        self.assertEqual(transport.requests, [])

    def test_sandbox_and_production_hosts_differ(self):
        transport = FakeTransport({"/query": json_response(qbo_query("Bill", []))})
        client(transport, environment="sandbox").get_bills()
        self.assertIn("sandbox-quickbooks.api.intuit.com", transport.last.url)

        transport = FakeTransport({"/query": json_response(qbo_query("Bill", []))})
        client(transport, environment="production").get_bills()
        self.assertIn("https://quickbooks.api.intuit.com", transport.last.url)
        self.assertNotIn("sandbox", transport.last.url)

    def test_unknown_environment_falls_back_to_sandbox(self):
        transport = FakeTransport({"/query": json_response(qbo_query("Bill", []))})
        client(transport, environment="staging").get_bills()
        self.assertIn("sandbox-quickbooks", transport.last.url)

    def test_missing_company_id_is_a_clear_error(self):
        transport = FakeTransport()
        naked = QuickBooksLedgerClient(
            CREDENTIALS, transport=transport, token=live_token(tenant_id=""), company_id=""
        )
        with self.assertRaises(LedgerTransportError):
            naked.get_bills()
        self.assertEqual(transport.requests, [])


class QueryConstructionTests(NoNetworkMixin, SimpleTestCase):
    def test_paging_table(self):
        for (num, offset), expected in PAGING_CASES:
            with self.subTest(num=num, offset=offset):
                transport = FakeTransport({"/query": json_response(qbo_query("Bill", []))})
                client(transport).get_bills(num, offset=offset)
                self.assertIn(expected, transport.last.params["query"])

    def test_vendor_filter_is_applied(self):
        transport = FakeTransport({"/query": json_response(qbo_query("Bill", []))})
        client(transport).get_bills(50, "56")
        self.assertIn("WHERE VendorRef = '56'", transport.last.params["query"])

    def test_no_vendor_filter_when_none(self):
        transport = FakeTransport({"/query": json_response(qbo_query("Bill", []))})
        client(transport).get_bills(50, None)
        self.assertNotIn("WHERE", transport.last.params["query"])

    def test_quotes_in_a_vendor_id_are_escaped(self):
        transport = FakeTransport({"/query": json_response(qbo_query("Bill", []))})
        client(transport).get_bills(50, "56' OR '1'='1")
        query = transport.last.params["query"]
        self.assertIn(r"\'", query)
        self.assertNotIn("OR '1'='1'", query)

    def test_minor_version_and_auth_header_are_sent(self):
        transport = FakeTransport({"/query": json_response(qbo_query("Bill", []))})
        client(transport).get_bills()
        self.assertEqual(transport.last.params["minorversion"], MINOR_VERSION)
        self.assertEqual(transport.last.headers["Authorization"], "Bearer test-access-token")
        self.assertEqual(transport.last.headers["Accept"], "application/json")

    def test_company_id_is_in_the_path(self):
        transport = FakeTransport({"/query": json_response(qbo_query("Bill", []))})
        client(transport).get_bills()
        self.assertIn(f"/v3/company/{COMPANY}/query", transport.last.url)


class MappingTests(NoNetworkMixin, SimpleTestCase):
    def test_bill_fields_map_exactly(self):
        transport = FakeTransport({"/query": json_response(qbo_query("Bill", [QBO_BILL]))})
        bill = client(transport).get_bills().items[0]
        self.assertEqual(bill.provider, "quickbooks")
        self.assertEqual(bill.id, "147")
        self.assertEqual(bill.document_number, "BILL-0147")
        self.assertEqual(bill.vendor_id, "56")
        self.assertEqual(bill.vendor_name, "Norton Lumber and Building Materials")
        self.assertEqual(bill.total, Decimal("1234.56"))
        self.assertEqual(bill.balance, Decimal("234.56"))
        self.assertEqual(bill.currency, "USD")
        self.assertEqual(bill.issued_on, date(2024, 3, 4))
        self.assertEqual(bill.due_on, date(2024, 4, 3))
        self.assertFalse(bill.is_paid)

    def test_zero_balance_bill_is_paid(self):
        transport = FakeTransport({
            "/query": json_response(qbo_query("Bill", [QBO_BILL_PAID]))
        })
        self.assertTrue(client(transport).get_bills().items[0].is_paid)

    def test_vendor_fields_map_exactly(self):
        transport = FakeTransport({"/query": json_response(qbo_query("Vendor", [QBO_VENDOR]))})
        vendor = client(transport).get_vendors().items[0]
        self.assertEqual(vendor.id, "56")
        self.assertEqual(vendor.name, "Norton Lumber and Building Materials")
        self.assertEqual(vendor.balance, Decimal("8975.25"))
        self.assertEqual(vendor.email, "ap@nortonlumber.example")
        self.assertEqual(vendor.currency, "USD")
        self.assertTrue(vendor.active)

    def test_sparse_row_does_not_explode(self):
        transport = FakeTransport({"/query": json_response(qbo_query("Bill", [{"Id": "9"}]))})
        bill = client(transport).get_bills().items[0]
        self.assertEqual(bill.id, "9")
        self.assertEqual(bill.vendor_id, "")
        self.assertEqual(bill.balance, Decimal("0.00"))
        self.assertIsNone(bill.due_on)

    def test_empty_query_response_yields_an_empty_page(self):
        transport = FakeTransport({"/query": json_response({"QueryResponse": {}})})
        page = client(transport).get_bills()
        self.assertEqual(len(page), 0)
        self.assertIsNone(page.next_offset)


class PaginationTests(NoNetworkMixin, SimpleTestCase):
    def test_full_page_advertises_a_next_offset(self):
        rows = [dict(QBO_BILL, Id=str(i)) for i in range(10)]
        transport = FakeTransport({"/query": json_response(qbo_query("Bill", rows))})
        page = client(transport).get_bills(10, offset=20)
        self.assertEqual(page.offset, 20)
        self.assertEqual(page.limit, 10)
        self.assertEqual(page.next_offset, 30)
        self.assertTrue(page.has_more)

    def test_short_page_ends_the_collection(self):
        rows = [dict(QBO_BILL, Id=str(i)) for i in range(3)]
        transport = FakeTransport({"/query": json_response(qbo_query("Bill", rows))})
        page = client(transport).get_bills(10)
        self.assertIsNone(page.next_offset)
        self.assertFalse(page.has_more)

    def test_iter_bills_pages_until_a_short_page(self):
        pages = [
            qbo_query("Bill", [dict(QBO_BILL, Id=f"a{i}") for i in range(2)]),
            qbo_query("Bill", [dict(QBO_BILL, Id=f"b{i}") for i in range(2)]),
            qbo_query("Bill", [dict(QBO_BILL, Id="c0")]),
        ]
        calls = {"n": 0}

        def handler(_request):
            payload = pages[min(calls["n"], len(pages) - 1)]
            calls["n"] += 1
            return json_response(payload)

        transport = FakeTransport({"/query": handler})
        ids = [bill.id for bill in client(transport).iter_bills(page_size=2)]
        self.assertEqual(ids, ["a0", "a1", "b0", "b1", "c0"])
        self.assertEqual(calls["n"], 3)

    def test_iter_bills_stops_at_the_page_ceiling(self):
        rows = qbo_query("Bill", [dict(QBO_BILL, Id="x"), dict(QBO_BILL, Id="y")])
        transport = FakeTransport({"/query": json_response(rows)})
        collected = list(client(transport).iter_bills(page_size=2, max_pages=4))
        self.assertEqual(len(collected), 8)
        self.assertEqual(len(transport.requests), 4)


class SingleRecordTests(NoNetworkMixin, SimpleTestCase):
    def test_get_bill_uses_the_id_it_is_given(self):
        transport = FakeTransport({"/bill/": json_response({"Bill": QBO_BILL})})
        bill = client(transport).get_bill("147")
        self.assertEqual(bill.id, "147")
        self.assertIn(f"/v3/company/{COMPANY}/bill/147", transport.last.url)

    def test_get_vendor_uses_the_id_it_is_given(self):
        transport = FakeTransport({"/vendor/": json_response({"Vendor": QBO_VENDOR})})
        client(transport).get_vendor("56")
        self.assertIn(f"/v3/company/{COMPANY}/vendor/56", transport.last.url)

    def test_blank_id_is_rejected_without_a_request(self):
        transport = FakeTransport()
        with self.assertRaises(LedgerNotFound):
            client(transport).get_bill("")
        with self.assertRaises(LedgerNotFound):
            client(transport).get_vendor("")
        self.assertEqual(transport.requests, [])

    def test_missing_envelope_is_a_not_found(self):
        transport = FakeTransport({"/bill/": json_response({"time": "2024-01-01"})})
        with self.assertRaises(LedgerNotFound):
            client(transport).get_bill("999")


class ErrorTranslationTests(NoNetworkMixin, SimpleTestCase):
    def test_404_becomes_not_found(self):
        transport = FakeTransport({"/query": json_response({"Fault": {}}, 404)})
        with self.assertRaises(LedgerNotFound):
            client(transport).get_bills()

    def test_401_becomes_auth_error(self):
        transport = FakeTransport({"/query": json_response({"Fault": {}}, 401)})
        with self.assertRaises(LedgerAuthError):
            client(transport).get_bills()

    def test_403_becomes_auth_error(self):
        transport = FakeTransport({"/query": json_response({"Fault": {}}, 403)})
        with self.assertRaises(LedgerAuthError):
            client(transport).get_bills()

    def test_500_becomes_transport_error_carrying_the_status(self):
        transport = FakeTransport({"/query": json_response({"Fault": {}}, 500)})
        with self.assertRaises(LedgerTransportError) as ctx:
            client(transport).get_bills()
        self.assertEqual(ctx.exception.status_code, 500)

    def test_html_error_page_is_a_transport_error_not_a_crash(self):
        transport = FakeTransport({"/query": json_response(None, 200)})
        with self.assertRaises(LedgerTransportError):
            client(transport).get_bills()


class ConnectTests(NoNetworkMixin, SimpleTestCase):
    def test_realm_id_from_the_callback_becomes_the_company_id(self):
        transport = FakeTransport({
            "/tokens/bearer": json_response(
                {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}
            )
        })
        fresh = QuickBooksLedgerClient(CREDENTIALS, transport=transport)
        token = fresh.connect("auth-code", realm_id="9130350000000001")
        self.assertEqual(fresh.company_id, "9130350000000001")
        self.assertEqual(token.tenant_id, "9130350000000001")
        self.assertTrue(fresh.is_connected)
