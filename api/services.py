"""Application layer: the only place Django and the ledger library meet.

Views below this call one method and serialise the result; the ledger package
above it knows nothing about Django. All the orchestration — reading provider
config out of settings, holding the shared HTTP transport, checking the OAuth
state parameter, persisting tokens — happens here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from django.conf import settings

from .ledger import (
    DEFAULT_PAGE_SIZE,
    Bill,
    LedgerAuthError,
    LedgerClientBase,
    Page,
    RequestsTransport,
    Transport,
    UnknownProviderError,
    Vendor,
    available_providers,
    build_client,
)
from .tokens import TokenStore

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: One pooled session for the whole process. Building a transport per request
#: was measurably the largest avoidable cost in the original code: it forced a
#: fresh TLS handshake for every outbound call.
_SHARED_TRANSPORT: Transport | None = None


def shared_transport() -> Transport:
    global _SHARED_TRANSPORT
    if _SHARED_TRANSPORT is None:
        _SHARED_TRANSPORT = RequestsTransport(
            timeout=float(getattr(settings, "LEDGER_HTTP_TIMEOUT", 15.0))
        )
    return _SHARED_TRANSPORT


def provider_config(provider: str) -> Mapping[str, Any]:
    configured = getattr(settings, "LEDGER_PROVIDERS", {})
    if provider not in configured:
        raise UnknownProviderError(
            f"ledger provider {provider!r} is not configured; configured providers: "
            f"{', '.join(sorted(configured)) or 'none'}"
        )
    if provider not in available_providers():
        raise UnknownProviderError(f"ledger provider {provider!r} has no implementation")
    return configured[provider]


def default_provider() -> str:
    return getattr(settings, "LEDGER_DEFAULT_PROVIDER", "quickbooks")


class LedgerService:
    """Drives one provider on behalf of one caller."""

    def __init__(self, store: TokenStore, *, transport: Transport | None = None) -> None:
        self.store = store
        self.transport = transport or shared_transport()

    # -- connection ----------------------------------------------------

    def client(self, provider: str) -> LedgerClientBase:
        """Build a client, armed with a stored token when one exists."""
        return build_client(
            provider,
            provider_config(provider),
            transport=self.transport,
            token=self.store.get(provider),
        )

    def start_authorization(self, provider: str) -> str:
        """Return the consent URL and remember the CSRF state."""
        url, state = self.client(provider)._get_connection()
        self.store.put_state(provider, state)
        logger.info("ledger: starting authorization for %s", provider)
        return url

    def complete_authorization(self, provider: str, code: str, state: str,
                               **provider_kwargs) -> LedgerClientBase:
        """Validate the state, exchange the code, and persist the token."""
        expected = self.store.pop_state(provider)
        if not expected or not state or state != expected:
            raise LedgerAuthError(
                f"{provider}: OAuth state mismatch; the callback did not come "
                f"from the authorization request this session started"
            )
        client = self.client(provider)
        token = client.connect(code, **provider_kwargs)
        self.store.set(provider, token)
        logger.info("ledger: %s connected (tenant=%s)", provider, token.tenant_id or "-")
        return client

    def disconnect(self, provider: str) -> None:
        self.store.clear(provider)

    # -- reads ---------------------------------------------------------

    def _read(self, provider: str, read: Callable[[LedgerClientBase], T]) -> T:
        """Run one read and keep any token the client refreshed along the way.

        The ``finally`` matters: Xero rotates refresh tokens, so once a refresh
        has happened the old refresh token is dead even if the read itself then
        fails with a 404.
        """
        client = self.client(provider)
        issued = client.current_token
        try:
            return read(client)
        finally:
            refreshed = client.current_token
            if refreshed is not None and refreshed is not issued:
                self.store.set(provider, refreshed)
                logger.info("ledger: stored refreshed %s token", provider)

    def list_bills(self, provider: str, *, limit: int = DEFAULT_PAGE_SIZE,
                   offset: int = 0, vendor_id: str | None = None) -> Page[Bill]:
        return self._read(
            provider, lambda client: client.get_bills(limit, vendor_id, offset=offset)
        )

    def get_bill(self, provider: str, bill_id: str) -> Bill:
        return self._read(provider, lambda client: client.get_bill(bill_id))

    def list_vendors(self, provider: str, *, limit: int = DEFAULT_PAGE_SIZE,
                     offset: int = 0) -> Page[Vendor]:
        return self._read(provider, lambda client: client.get_vendors(limit, offset=offset))

    def get_vendor(self, provider: str, vendor_id: str) -> Vendor:
        return self._read(provider, lambda client: client.get_vendor(vendor_id))

    def connection_summary(self, provider: str, *, limit: int = DEFAULT_PAGE_SIZE) -> dict:
        """First page of each collection — what the callback reports back.

        Deliberately one page of each rather than a full sync: a company with
        20,000 bills must not be able to turn an OAuth callback into a
        multi-minute request.
        """
        def first_pages(client: LedgerClientBase) -> dict:
            return {
                "provider": provider,
                "connected": client.is_connected,
                "bills": client.get_bills(limit).as_dict(),
                "vendors": client.get_vendors(limit).as_dict(),
            }

        return self._read(provider, first_pages)
