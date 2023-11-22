#!/usr/bin/env python3
"""A stand-in for the QuickBooks Online API, for local demos and transcripts.

The service talks to Intuit over HTTPS, so without sandbox credentials there is
no way to see a real response flow through it end to end. This script serves
the handful of endpoints the QuickBooks provider calls, using the same URL
shapes, query language and response envelopes, so the app can be driven for
real against ``QBOOK_API_BASE`` / ``QBOOK_TOKEN_URL``.

It is a development aid, not part of the service. Nothing in ``api/`` imports
it, and it is excluded from the Docker build context.

    python docs/local_provider_stub.py 8871

Then, in another shell:

    DJANGO_DEBUG=true \\
    DJANGO_SECRET_KEY=dev-only \\
    QBOOK_CLIENT_ID=demo-id QBOOK_CLIENT_SECRET=demo-secret \\
    QBOOK_REDIRECT_URI=http://127.0.0.1:8870/accounts/quickbooks/login/callback/ \\
    QBOOK_API_BASE=http://127.0.0.1:8871/qbo \\
    QBOOK_TOKEN_URL=http://127.0.0.1:8871/oauth/token \\
    python manage.py runserver 127.0.0.1:8870
"""

from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VENDORS = [
    {"Id": "56", "DisplayName": "Norton Lumber and Building Materials",
     "Balance": 8975.25, "Active": True, "CurrencyRef": {"value": "USD"},
     "PrimaryEmailAddr": {"Address": "ap@nortonlumber.example"}},
    {"Id": "57", "DisplayName": "Hicks Hardware", "Balance": 1200.00, "Active": True,
     "CurrencyRef": {"value": "USD"},
     "PrimaryEmailAddr": {"Address": "billing@hickshardware.example"}},
    {"Id": "58", "DisplayName": "Tania's Nursery", "Balance": 0, "Active": True,
     "CurrencyRef": {"value": "USD"}},
    {"Id": "59", "DisplayName": "Ellis Equipment Rental", "Balance": 4310.40,
     "Active": False, "CurrencyRef": {"value": "USD"}},
]

BILLS = [
    {"Id": "141", "DocNumber": "BILL-0141", "TxnDate": "2024-03-01", "DueDate": "2024-03-31",
     "TotalAmt": 1500.00, "Balance": 1500.00, "CurrencyRef": {"value": "USD"},
     "VendorRef": {"value": "56", "name": "Norton Lumber and Building Materials"}},
    {"Id": "142", "DocNumber": "BILL-0142", "TxnDate": "2024-03-02", "DueDate": "2024-04-01",
     "TotalAmt": 275.50, "Balance": 0, "CurrencyRef": {"value": "USD"},
     "VendorRef": {"value": "57", "name": "Hicks Hardware"}},
    {"Id": "143", "DocNumber": "BILL-0143", "TxnDate": "2024-03-03", "DueDate": "2024-04-02",
     "TotalAmt": 89.99, "Balance": 89.99, "CurrencyRef": {"value": "USD"},
     "VendorRef": {"value": "58", "name": "Tania's Nursery"}},
    {"Id": "144", "DocNumber": "BILL-0144", "TxnDate": "2024-03-04", "DueDate": "2024-04-03",
     "TotalAmt": 4310.40, "Balance": 4310.40, "CurrencyRef": {"value": "USD"},
     "VendorRef": {"value": "59", "name": "Ellis Equipment Rental"}},
    {"Id": "145", "DocNumber": "BILL-0145", "TxnDate": "2024-03-05", "DueDate": "2024-04-04",
     "TotalAmt": 620.00, "Balance": 120.00, "CurrencyRef": {"value": "USD"},
     "VendorRef": {"value": "56", "name": "Norton Lumber and Building Materials"}},
    {"Id": "146", "DocNumber": "BILL-0146", "TxnDate": "2024-03-06", "DueDate": "2024-04-05",
     "TotalAmt": 42.00, "Balance": 42.00, "CurrencyRef": {"value": "USD"},
     "VendorRef": {"value": "57", "name": "Hicks Hardware"}},
    {"Id": "147", "DocNumber": "BILL-0147", "TxnDate": "2024-03-07", "DueDate": "2024-04-06",
     "TotalAmt": 1234.56, "Balance": 234.56, "CurrencyRef": {"value": "USD"},
     "VendorRef": {"value": "56", "name": "Norton Lumber and Building Materials"}},
]

_PAGING = re.compile(r"STARTPOSITION (\d+) MAXRESULTS (\d+)")
_VENDOR_FILTER = re.compile(r"VendorRef = '([^']*)'")


def run_query(statement: str) -> dict:
    """Interpret the small subset of the QuickBooks query language we emit."""
    entity = "Vendor" if " FROM Vendor" in statement else "Bill"
    rows = VENDORS if entity == "Vendor" else BILLS

    vendor_filter = _VENDOR_FILTER.search(statement)
    if vendor_filter:
        wanted = vendor_filter.group(1).replace("\\'", "'")
        rows = [r for r in rows if r.get("VendorRef", {}).get("value") == wanted]

    paging = _PAGING.search(statement)
    start, count = (int(paging.group(1)), int(paging.group(2))) if paging else (1, 50)
    window = rows[start - 1: start - 1 + count]
    return {"QueryResponse": ({entity: window} if window else {}) | {
        "startPosition": start, "maxResults": len(window)},
        "time": "2024-06-01T00:00:00.000-07:00"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A002 - BaseHTTPRequestHandler API
        sys.stderr.write("stub: " + fmt % args + "\n")

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if urlparse(self.path).path == "/oauth/token":
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._send({
                "access_token": "stub-access-token",
                "refresh_token": "stub-refresh-token",
                "token_type": "bearer",
                "expires_in": 3600,
            })
            return
        self._send({"error": "not_found"}, 404)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            self._send({"Fault": {"type": "AUTHENTICATION"}}, 401)
            return

        parts = parsed.path.strip("/").split("/")
        # /qbo/v3/company/<realm>/<resource>[/<id>]
        if len(parts) < 5 or parts[1:3] != ["v3", "company"]:
            self._send({"Fault": {"type": "NOT_FOUND"}}, 404)
            return
        resource = parts[4]
        record_id = parts[5] if len(parts) > 5 else ""

        if resource == "query":
            statement = parse_qs(parsed.query).get("query", [""])[0]
            self._send(run_query(statement))
        elif resource == "bill":
            match = next((b for b in BILLS if b["Id"] == record_id), None)
            self._send({"Bill": match} if match else {"Fault": {}}, 200 if match else 404)
        elif resource == "vendor":
            match = next((v for v in VENDORS if v["Id"] == record_id), None)
            self._send({"Vendor": match} if match else {"Fault": {}}, 200 if match else 404)
        else:
            self._send({"Fault": {"type": "NOT_FOUND"}}, 404)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8871
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"QuickBooks stub listening on http://127.0.0.1:{port}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
