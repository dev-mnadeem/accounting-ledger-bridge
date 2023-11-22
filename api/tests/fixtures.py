"""Recorded-shape payloads from the QuickBooks and Xero Accounting APIs.

These are hand-written to the published response shapes, with values chosen so
that every mapped field is distinguishable from every other (no two amounts or
ids collide), which is what makes a mis-wired mapping visible in an assertion.
"""

from __future__ import annotations

QBO_BILL = {
    "Id": "147",
    "DocNumber": "BILL-0147",
    "TxnDate": "2024-03-04",
    "DueDate": "2024-04-03",
    "TotalAmt": 1234.56,
    "Balance": 234.56,
    "CurrencyRef": {"value": "USD", "name": "United States Dollar"},
    "VendorRef": {"value": "56", "name": "Norton Lumber and Building Materials"},
}

QBO_BILL_PAID = {
    "Id": "148",
    "DocNumber": "BILL-0148",
    "TxnDate": "2024-03-05",
    "DueDate": "2024-04-04",
    "TotalAmt": 900.00,
    "Balance": 0,
    "CurrencyRef": {"value": "USD"},
    "VendorRef": {"value": "57", "name": "Hicks Hardware"},
}

QBO_VENDOR = {
    "Id": "56",
    "DisplayName": "Norton Lumber and Building Materials",
    "Balance": 8975.25,
    "Active": True,
    "CurrencyRef": {"value": "USD"},
    "PrimaryEmailAddr": {"Address": "ap@nortonlumber.example"},
}

XERO_INVOICE = {
    "InvoiceID": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "InvoiceNumber": "INV-0042",
    "Type": "ACCPAY",
    "DateString": "2024-05-06T00:00:00",
    "DueDateString": "2024-06-05T00:00:00",
    "Total": "4500.75",
    "AmountDue": "1500.25",
    "CurrencyCode": "GBP",
    "Contact": {
        "ContactID": "b1c2d3e4-0000-4000-8000-000000000001",
        "Name": "Ridgeway Supplies Ltd",
    },
}

XERO_CONTACT = {
    "ContactID": "b1c2d3e4-0000-4000-8000-000000000001",
    "Name": "Ridgeway Supplies Ltd",
    "EmailAddress": "accounts@ridgeway.example",
    "ContactStatus": "ACTIVE",
    "DefaultCurrency": "GBP",
    "Balances": {"AccountsPayable": {"Outstanding": "1500.25", "Overdue": "0.00"}},
}


def qbo_query(key: str, rows: list[dict]) -> dict:
    return {"QueryResponse": {key: rows, "startPosition": 1, "maxResults": len(rows)},
            "time": "2024-06-01T00:00:00.000-07:00"}
