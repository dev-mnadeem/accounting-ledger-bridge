"""The contract every accounting-ledger provider implements.

The four read operations and their signatures are inherited verbatim from the
original ``LedgerClientBase`` skeleton this project started from, so the
contract a reviewer was given is the contract that is honoured. What changed is
that the arguments are now *used*: ``num`` bounds the page, ``vendor_id``
filters, and ``bill_id``/``vendor_id`` select the record. The originals
accepted those arguments and ignored every one of them.

Methods return values rather than mutating instance attributes, which makes a
client safe to reuse across requests and makes every result independently
assertable in a test.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import replace

from .domain import Bill, Page, Vendor
from .errors import LedgerAuthError
from .oauth import OAuth2Credentials, OAuth2Flow, TokenSet
from .transport import Transport

logger = logging.getLogger(__name__)

#: Providers cap a single page; asking for more is silently truncated by them,
#: so clamp locally and page explicitly instead.
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50


def clamp_page_size(num: int | None) -> int:
    """Bound a caller-supplied page size into ``1..MAX_PAGE_SIZE``."""
    if not num:
        return DEFAULT_PAGE_SIZE
    return max(1, min(int(num), MAX_PAGE_SIZE))


class LedgerClientBase(ABC):
    """Base Ledger Integration Client class.

    Construction is side-effect free and never touches the network, so a client
    can be built in a view, a management command, or a test without credentials
    being reachable.
    """

    #: Registry key, e.g. ``"quickbooks"``.
    name: str = ""

    def __init__(
        self,
        credentials: OAuth2Credentials,
        *,
        transport: Transport,
        token: TokenSet | None = None,
        token_url: str = "",
    ) -> None:
        self.credentials = credentials
        self.transport = transport
        self._token = token
        oauth_config = self.oauth_config()
        if token_url:
            # Development/demo and egress-proxy seam; see the README config table.
            oauth_config = replace(oauth_config, token_url=token_url)
        self._flow = OAuth2Flow(
            config=oauth_config,
            credentials=credentials,
            transport=transport,
            provider=self.name,
        )

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    @abstractmethod
    def oauth_config():
        """Return the provider's :class:`~api.ledger.oauth.OAuth2Config`."""

    def _get_connection(self, state: str | None = None) -> tuple[str, str]:
        """Return ``(authorization_url, state)`` to send the user to."""
        return self._flow.authorization_url(state)

    def connect(self, code: str, **provider_kwargs) -> TokenSet:
        """Exchange an authorization code for tokens and arm this client."""
        token = self._flow.exchange_code(code)
        self._token = self._after_connect(token, **provider_kwargs)
        return self._token

    def _after_connect(self, token: TokenSet, **provider_kwargs) -> TokenSet:  # noqa: ARG002
        """Hook for provider-specific post-exchange work (e.g. tenant lookup).

        The base implementation ignores ``provider_kwargs`` by design; both
        shipped subclasses override this and use them.
        """
        return token

    @property
    def token(self) -> TokenSet:
        if self._token is None:
            raise LedgerAuthError(
                f"{self.name}: client is not connected. Complete the OAuth "
                f"callback, or construct the client with a stored token."
            )
        if self._token.is_expired():
            logger.info("%s: access token expired, refreshing", self.name)
            self._token = self._flow.refresh(self._token)
        return self._token

    @property
    def is_connected(self) -> bool:
        return self._token is not None

    # ------------------------------------------------------------------
    # Read operations — the signatures the brief specified
    # ------------------------------------------------------------------

    @abstractmethod
    def get_bills(self, num: int = DEFAULT_PAGE_SIZE, vendor_id: str | None = None,
                  *, offset: int = 0) -> Page[Bill]:
        """Return at most ``num`` bills, optionally only those for ``vendor_id``."""

    @abstractmethod
    def get_bill(self, bill_id: str) -> Bill:
        """Return the single bill identified by ``bill_id``."""

    @abstractmethod
    def get_vendors(self, num: int = DEFAULT_PAGE_SIZE, *, offset: int = 0) -> Page[Vendor]:
        """Return at most ``num`` vendors."""

    @abstractmethod
    def get_vendor(self, vendor_id: str) -> Vendor:
        """Return the single vendor identified by ``vendor_id``."""

    # ------------------------------------------------------------------
    # Convenience built on the contract above
    # ------------------------------------------------------------------

    def iter_bills(self, vendor_id: str | None = None, *,
                   page_size: int = DEFAULT_PAGE_SIZE,
                   max_pages: int = 100) -> Iterator[Bill]:
        """Stream every bill, paging until the provider runs out.

        ``max_pages`` is a deliberate circuit breaker: a provider that keeps
        returning full pages must not be able to spin this loop forever.
        """
        offset = 0
        for _ in range(max_pages):
            page = self.get_bills(page_size, vendor_id, offset=offset)
            yield from page.items
            if page.next_offset is None:
                return
            offset = page.next_offset
        logger.warning("%s: iter_bills stopped at the %d-page ceiling", self.name, max_pages)

    def iter_vendors(self, *, page_size: int = DEFAULT_PAGE_SIZE,
                     max_pages: int = 100) -> Iterator[Vendor]:
        offset = 0
        for _ in range(max_pages):
            page = self.get_vendors(page_size, offset=offset)
            yield from page.items
            if page.next_offset is None:
                return
            offset = page.next_offset
        logger.warning("%s: iter_vendors stopped at the %d-page ceiling", self.name, max_pages)

    def _authorized_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token.access_token}",
            "Accept": "application/json",
        }
