"""QuickBooks Online provider.

Talks to the Accounting API v3 over plain HTTPS rather than through the
``python-quickbooks`` / ``intuit-oauth`` SDK pair. That swap is deliberate and
is explained in the README design notes: those packages pin ``intuit-oauth==
1.2.4``, which depends on ``future``, which imports the ``imp`` module removed
in Python 3.12 — the project could never run on a Python newer than 3.11. The
SDK also performed a live HTTPS request inside ``AuthClient.__init__``, which
made every client construction a network call and every unit test an
integration test.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import DEFAULT_PAGE_SIZE, LedgerClientBase, clamp_page_size
from .domain import Bill, Page, Vendor, to_date, to_decimal
from .errors import LedgerNotFound, LedgerTransportError
from .oauth import OAuth2Config
from .transport import raise_for_status

AUTHORIZE_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
SCOPES = ("com.intuit.quickbooks.accounting",)

API_BASE = {
    "sandbox": "https://sandbox-quickbooks.api.intuit.com",
    "production": "https://quickbooks.api.intuit.com",
}
MINOR_VERSION = "70"


def _escape(value: str) -> str:
    """Escape a literal for a QuickBooks query string.

    QuickBooks' query language is SQL-like and interpolated into a URL
    parameter, so a vendor id containing a quote would otherwise change the
    shape of the query.
    """
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


class QuickBooksLedgerClient(LedgerClientBase):
    """Read bills and vendors from a connected QuickBooks Online company."""

    name = "quickbooks"

    def __init__(self, credentials, *, transport, token=None,
                 environment: str = "sandbox", company_id: str = "",
                 api_base: str = "", token_url: str = "") -> None:
        self.environment = (environment or "sandbox").lower()
        self.company_id = company_id
        #: Overrides the environment-derived host. Exists so the service can be
        #: pointed at a recording proxy or a local stub during development and
        #: demos; unset in every real deployment.
        self.api_base_override = api_base.rstrip("/")
        super().__init__(credentials, transport=transport, token=token,
                         token_url=token_url)

    @staticmethod
    def oauth_config() -> OAuth2Config:
        return OAuth2Config(authorize_url=AUTHORIZE_URL, token_url=TOKEN_URL, scopes=SCOPES)

    def _after_connect(self, token, **provider_kwargs):
        # QuickBooks returns the company ("realm") id on the callback, not in
        # the token payload, so the view passes it through to here.
        realm_id = provider_kwargs.get("realm_id") or ""
        if realm_id:
            self.company_id = str(realm_id)
        token.tenant_id = self.company_id
        return token

    # ------------------------------------------------------------------

    @property
    def api_base(self) -> str:
        return self.api_base_override or API_BASE.get(self.environment, API_BASE["sandbox"])

    def _company_url(self, path: str) -> str:
        company_id = self.company_id or self.token.tenant_id
        if not company_id:
            raise LedgerTransportError(
                "quickbooks: no company (realm) id; complete the OAuth callback first"
            )
        return f"{self.api_base}/v3/company/{company_id}/{path}"

    def _get(self, path: str, params: dict[str, Any], *, context: str) -> Mapping[str, Any]:
        params = {**params, "minorversion": MINOR_VERSION}
        response = self.transport.request(
            "GET", self._company_url(path), params=params, headers=self._authorized_headers()
        )
        raise_for_status(response, context=context)
        if not isinstance(response.json, Mapping):
            raise LedgerTransportError(f"{context}: expected a JSON object")
        return response.json

    def _query(self, statement: str, *, context: str) -> Mapping[str, Any]:
        payload = self._get("query", {"query": statement}, context=context)
        node = payload.get("QueryResponse")
        return node if isinstance(node, Mapping) else {}

    # ------------------------------------------------------------------
    # Contract
    # ------------------------------------------------------------------

    def get_bills(self, num: int = DEFAULT_PAGE_SIZE, vendor_id: str | None = None,
                  *, offset: int = 0) -> Page[Bill]:
        limit = clamp_page_size(num)
        where = f" WHERE VendorRef = '{_escape(vendor_id)}'" if vendor_id else ""
        # STARTPOSITION is 1-based in QuickBooks; our offsets are 0-based.
        statement = (
            f"SELECT * FROM Bill{where} ORDER BY Id "
            f"STARTPOSITION {offset + 1} MAXRESULTS {limit}"
        )
        rows = self._query(statement, context="quickbooks get_bills").get("Bill") or []
        items = tuple(self._to_bill(row) for row in rows)
        return Page(
            items=items,
            offset=offset,
            limit=limit,
            next_offset=offset + limit if len(items) == limit else None,
        )

    def get_bill(self, bill_id: str) -> Bill:
        if not bill_id:
            raise LedgerNotFound("quickbooks get_bill: no bill id supplied")
        payload = self._get(f"bill/{bill_id}", {}, context=f"quickbooks get_bill({bill_id})")
        row = payload.get("Bill")
        if not isinstance(row, Mapping):
            raise LedgerNotFound(f"quickbooks: bill {bill_id} not found")
        return self._to_bill(row)

    def get_vendors(self, num: int = DEFAULT_PAGE_SIZE, *, offset: int = 0) -> Page[Vendor]:
        limit = clamp_page_size(num)
        statement = (
            f"SELECT * FROM Vendor ORDER BY Id "
            f"STARTPOSITION {offset + 1} MAXRESULTS {limit}"
        )
        rows = self._query(statement, context="quickbooks get_vendors").get("Vendor") or []
        items = tuple(self._to_vendor(row) for row in rows)
        return Page(
            items=items,
            offset=offset,
            limit=limit,
            next_offset=offset + limit if len(items) == limit else None,
        )

    def get_vendor(self, vendor_id: str) -> Vendor:
        if not vendor_id:
            raise LedgerNotFound("quickbooks get_vendor: no vendor id supplied")
        payload = self._get(
            f"vendor/{vendor_id}", {}, context=f"quickbooks get_vendor({vendor_id})"
        )
        row = payload.get("Vendor")
        if not isinstance(row, Mapping):
            raise LedgerNotFound(f"quickbooks: vendor {vendor_id} not found")
        return self._to_vendor(row)

    # ------------------------------------------------------------------
    # Mapping
    # ------------------------------------------------------------------

    def _to_bill(self, row: Mapping[str, Any]) -> Bill:
        vendor_ref = row.get("VendorRef") or {}
        return Bill(
            provider=self.name,
            id=str(row.get("Id") or ""),
            vendor_id=str(vendor_ref.get("value") or ""),
            vendor_name=str(vendor_ref.get("name") or ""),
            total=to_decimal(row.get("TotalAmt")),
            balance=to_decimal(row.get("Balance")),
            currency=str((row.get("CurrencyRef") or {}).get("value") or ""),
            document_number=str(row.get("DocNumber") or ""),
            issued_on=to_date(row.get("TxnDate")),
            due_on=to_date(row.get("DueDate")),
        )

    def _to_vendor(self, row: Mapping[str, Any]) -> Vendor:
        return Vendor(
            provider=self.name,
            id=str(row.get("Id") or ""),
            name=str(row.get("DisplayName") or ""),
            balance=to_decimal(row.get("Balance")),
            currency=str((row.get("CurrencyRef") or {}).get("value") or ""),
            email=str((row.get("PrimaryEmailAddr") or {}).get("Address") or ""),
            active=bool(row.get("Active", True)),
        )
