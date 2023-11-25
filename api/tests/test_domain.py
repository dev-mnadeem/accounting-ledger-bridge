"""Domain-type coercion and serialisation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from api.ledger.domain import Bill, Page, Vendor, format_amount, to_date, to_decimal

#: Independently derived: the exact JSON string each amount must serialise to.
#: Padding adds trailing zeros; extra precision is never rounded away.
FORMAT_CASES = [
    (Decimal("0"), "0.00"),
    (Decimal("0.00"), "0.00"),
    (Decimal("1500"), "1500.00"),
    (Decimal("1500.0"), "1500.00"),
    (Decimal("1500.00"), "1500.00"),
    (Decimal("4310.4"), "4310.40"),
    (Decimal("1234.56"), "1234.56"),
    (Decimal("-40.05"), "-40.05"),
    (Decimal("0.125"), "0.125"),
    (Decimal("12.3456789"), "12.3456789"),
]

#: Independently derived: value in, exact Decimal expected out.
DECIMAL_CASES = [
    (None, Decimal("0.00")),
    ("", Decimal("0.00")),
    (0, Decimal("0")),
    (1234.56, Decimal("1234.56")),
    ("1234.56", Decimal("1234.56")),
    ("0.1", Decimal("0.1")),
    (Decimal("19.99"), Decimal("19.99")),
    ("-40.05", Decimal("-40.05")),
    ("not-a-number", Decimal("0.00")),
    ([], Decimal("0.00")),
]

DATE_CASES = [
    (None, None),
    ("", None),
    ("2024-03-04", date(2024, 3, 4)),
    ("2024-03-04T00:00:00", date(2024, 3, 4)),
    ("04/03/2024", None),
    ("garbage", None),
]


class ToDecimalTests(SimpleTestCase):
    def test_table(self):
        for raw, expected in DECIMAL_CASES:
            with self.subTest(raw=raw):
                self.assertEqual(to_decimal(raw), expected)

    def test_float_amount_is_not_corrupted(self):
        # str(1234.56) round-trips exactly; Decimal(1234.56) would not.
        self.assertEqual(str(to_decimal(1234.56)), "1234.56")


class FormatAmountTests(SimpleTestCase):
    def test_table(self):
        for raw, expected in FORMAT_CASES:
            with self.subTest(raw=raw):
                self.assertEqual(format_amount(raw), expected)

    def test_extra_precision_is_never_rounded_away(self):
        # A rounded payable is a correctness bug; the string must stay exact.
        self.assertEqual(Decimal(format_amount(Decimal("0.005"))), Decimal("0.005"))


class ToDateTests(SimpleTestCase):
    def test_table(self):
        for raw, expected in DATE_CASES:
            with self.subTest(raw=raw):
                self.assertEqual(to_date(raw), expected)


class BillTests(SimpleTestCase):
    def _bill(self, balance: str) -> Bill:
        return Bill(
            provider="quickbooks",
            id="1",
            vendor_id="56",
            vendor_name="Norton",
            total=Decimal("100.00"),
            balance=Decimal(balance),
            currency="USD",
            document_number="B-1",
            issued_on=date(2024, 1, 2),
            due_on=date(2024, 2, 1),
        )

    def test_is_paid(self):
        self.assertTrue(self._bill("0.00").is_paid)
        self.assertTrue(self._bill("-1.00").is_paid)
        self.assertFalse(self._bill("0.01").is_paid)

    def test_as_dict_serialises_money_as_string(self):
        payload = self._bill("42.50").as_dict()
        self.assertEqual(payload["balance"], "42.50")
        self.assertEqual(payload["total"], "100.00")
        self.assertEqual(payload["issued_on"], "2024-01-02")
        self.assertEqual(payload["due_on"], "2024-02-01")
        self.assertIs(payload["is_paid"], False)

    def test_as_dict_handles_missing_dates(self):
        bill = Bill(provider="xero", id="x", vendor_id="", vendor_name="")
        self.assertIsNone(bill.as_dict()["issued_on"])
        self.assertIsNone(bill.as_dict()["due_on"])


class PageTests(SimpleTestCase):
    def test_has_more_follows_next_offset(self):
        vendors = (Vendor(provider="xero", id="1", name="A"),)
        self.assertTrue(Page(items=vendors, offset=0, limit=1, next_offset=1).has_more)
        self.assertFalse(Page(items=vendors, offset=0, limit=1, next_offset=None).has_more)

    def test_len_and_iteration(self):
        vendors = tuple(Vendor(provider="xero", id=str(i), name=f"V{i}") for i in range(3))
        page = Page(items=vendors, offset=0, limit=3)
        self.assertEqual(len(page), 3)
        self.assertEqual([v.id for v in page], ["0", "1", "2"])

    def test_as_dict(self):
        page = Page(items=(Vendor(provider="xero", id="1", name="A"),),
                    offset=10, limit=5, next_offset=15)
        payload = page.as_dict()
        self.assertEqual(payload["offset"], 10)
        self.assertEqual(payload["limit"], 5)
        self.assertEqual(payload["next_offset"], 15)
        self.assertTrue(payload["has_more"])
        self.assertEqual(payload["items"][0]["name"], "A")
