"""Xero provider.

``example.env`` has shipped ``XERO_CLIENT_ID`` / ``XERO_CLIENT_SECRET`` since
the first commit, but no Xero implementation ever existed — the abstract base
had exactly one subclass. This is that second subclass, and it is the reason
the base class earns its keep.

Xero models a payable as an invoice of type ``ACCPAY`` and a vendor as a
``Contact``, so the mapping below is where that vocabulary is translated into
the shared domain types.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import DEFAULT_PAGE_SIZE, LedgerClientBase, clamp_page_size
from .domain import Bill, Page, Vendor, to_date, to_decimal
from .errors import LedgerAuthError, LedgerNotFound, LedgerTransportError
from .oauth import OAuth2Config
from .transport import raise_for_status

AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"
API_BASE = "https://api.xero.com/api.xro/2.0"
SCOPES = (
    "openid",
    "profile",
    "offline_access",
    "accounting.transactions.read",
    "accounting.contacts.read",
)
PAYABLE_TYPE = "ACCPAY"


class XeroLedgerClient(LedgerClientBase):
    """Read payable invoices and supplier contacts from a Xero organisation."""

    name = "xero"

    def __init__(self, credentials, *, transport, token=None, tenant_id: str = "",
                 api_base: str = "", token_url: str = "") -> None:
        self.tenant_id = tenant_id
        #: See QuickBooksLedgerClient.api_base_override.
        self.api_base = (api_base or API_BASE).rstrip("/")
        #: Xero's tenant list sits alongside the API host, not under it.
        self.connections_url = (
            f"{self.api_base.rsplit('/api.xro', 1)[0]}/connections"
            if api_base
            else CONNECTIONS_URL
        )
        super().__init__(credentials, transport=transport, token=token,
                         token_url=token_url)

    @staticmethod
    def oauth_config() -> OAuth2Config:
        return OAuth2Config(authorize_url=AUTHORIZE_URL, token_url=TOKEN_URL, scopes=SCOPES)

    def _after_connect(self, token, **provider_kwargs):
        # Unlike QuickBooks, Xero does not name the organisation on the
        # callback: it has to be read back from /connections.
        token.tenant_id = provider_kwargs.get("tenant_id") or self._first_tenant(token)
        self.tenant_id = token.tenant_id
        return token

    def _first_tenant(self, token) -> str:
        response = self.transport.request(
            "GET",
            self.connections_url,
            headers={
                "Authorization": f"Bearer {token.access_token}",
                "Accept": "application/json",
            },
        )
        raise_for_status(response, context="xero connections")
        rows = response.json if isinstance(response.json, list) else []
        for row in rows:
            if isinstance(row, Mapping) and row.get("tenantId"):
                return str(row["tenantId"])
        raise LedgerAuthError("xero: the authorising user has no connected organisation")

    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        tenant = self.tenant_id or self.token.tenant_id
        if not tenant:
            raise LedgerTransportError(
                "xero: no tenant id; complete the OAuth callback first"
            )
        return {**self._authorized_headers(), "Xero-tenant-id": tenant}

    def _get(self, path: str, params: dict[str, Any], *, context: str) -> Mapping[str, Any]:
        response = self.transport.request(
            "GET", f"{self.api_base}/{path}", params=params, headers=self._headers()
        )
        raise_for_status(response, context=context)
        if not isinstance(response.json, Mapping):
            raise LedgerTransportError(f"{context}: expected a JSON object")
        return response.json

    # ------------------------------------------------------------------
    # Contract
    # ------------------------------------------------------------------

    def get_bills(self, num: int = DEFAULT_PAGE_SIZE, vendor_id: str | None = None,
                  *, offset: int = 0) -> Page[Bill]:
        limit = clamp_page_size(num)
        where = f'Type=="{PAYABLE_TYPE}"'
        if vendor_id:
            where += f'&&Contact.ContactID==Guid("{vendor_id}")'
        # Xero pages by 1-based page number, so a caller offset only lands on a
        # page boundary; normalise the offset to the page we actually request.
        page_number = offset // limit + 1
        payload = self._get(
            "Invoices",
            {"where": where, "page": page_number, "pageSize": limit, "order": "Date"},
            context="xero get_bills",
        )
        rows = payload.get("Invoices") or []
        items = tuple(self._to_bill(row) for row in rows)
        aligned_offset = (page_number - 1) * limit
        return Page(
            items=items,
            offset=aligned_offset,
            limit=limit,
            next_offset=aligned_offset + limit if len(items) == limit else None,
        )

    def get_bill(self, bill_id: str) -> Bill:
        if not bill_id:
            raise LedgerNotFound("xero get_bill: no invoice id supplied")
        payload = self._get(f"Invoices/{bill_id}", {}, context=f"xero get_bill({bill_id})")
        rows = payload.get("Invoices") or []
        if not rows:
            raise LedgerNotFound(f"xero: invoice {bill_id} not found")
        return self._to_bill(rows[0])

    def get_vendors(self, num: int = DEFAULT_PAGE_SIZE, *, offset: int = 0) -> Page[Vendor]:
        limit = clamp_page_size(num)
        page_number = offset // limit + 1
        payload = self._get(
            "Contacts",
            {"where": "IsSupplier==true", "page": page_number, "pageSize": limit},
            context="xero get_vendors",
        )
        rows = payload.get("Contacts") or []
        items = tuple(self._to_vendor(row) for row in rows)
        aligned_offset = (page_number - 1) * limit
        return Page(
            items=items,
            offset=aligned_offset,
            limit=limit,
            next_offset=aligned_offset + limit if len(items) == limit else None,
        )

    def get_vendor(self, vendor_id: str) -> Vendor:
        if not vendor_id:
            raise LedgerNotFound("xero get_vendor: no contact id supplied")
        payload = self._get(
            f"Contacts/{vendor_id}", {}, context=f"xero get_vendor({vendor_id})"
        )
        rows = payload.get("Contacts") or []
        if not rows:
            raise LedgerNotFound(f"xero: contact {vendor_id} not found")
        return self._to_vendor(rows[0])

    # ------------------------------------------------------------------
    # Mapping
    # ------------------------------------------------------------------

    def _to_bill(self, row: Mapping[str, Any]) -> Bill:
        contact = row.get("Contact") or {}
        return Bill(
            provider=self.name,
            id=str(row.get("InvoiceID") or ""),
            vendor_id=str(contact.get("ContactID") or ""),
            vendor_name=str(contact.get("Name") or ""),
            total=to_decimal(row.get("Total")),
            balance=to_decimal(row.get("AmountDue")),
            currency=str(row.get("CurrencyCode") or ""),
            document_number=str(row.get("InvoiceNumber") or ""),
            issued_on=to_date(row.get("DateString") or row.get("Date")),
            due_on=to_date(row.get("DueDateString") or row.get("DueDate")),
        )

    def _to_vendor(self, row: Mapping[str, Any]) -> Vendor:
        payable = ((row.get("Balances") or {}).get("AccountsPayable") or {})
        return Vendor(
            provider=self.name,
            id=str(row.get("ContactID") or ""),
            name=str(row.get("Name") or ""),
            balance=to_decimal(payable.get("Outstanding")),
            currency=str(row.get("DefaultCurrency") or ""),
            email=str(row.get("EmailAddress") or ""),
            active=str(row.get("ContactStatus") or "ACTIVE").upper() == "ACTIVE",
        )
