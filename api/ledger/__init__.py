"""Accounting-ledger integrations.

A framework-free library: providers, the OAuth 2.0 flow they share, the HTTP
transport seam, and the provider-neutral domain types. Nothing here imports
Django, so every piece is unit-testable on its own.
"""

from .base import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, LedgerClientBase
from .domain import Bill, Page, Vendor
from .errors import (
    LedgerAuthError,
    LedgerConfigurationError,
    LedgerError,
    LedgerNotFound,
    LedgerTransportError,
    UnknownProviderError,
)
from .oauth import OAuth2Config, OAuth2Credentials, OAuth2Flow, TokenSet
from .quickbooks import QuickBooksLedgerClient
from .registry import available_providers, build_client, get_provider_class, register
from .transport import RequestsTransport, Response, Transport
from .xero import XeroLedgerClient

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "Bill",
    "LedgerAuthError",
    "LedgerClientBase",
    "LedgerConfigurationError",
    "LedgerError",
    "LedgerNotFound",
    "LedgerTransportError",
    "OAuth2Config",
    "OAuth2Credentials",
    "OAuth2Flow",
    "Page",
    "QuickBooksLedgerClient",
    "RequestsTransport",
    "Response",
    "TokenSet",
    "Transport",
    "UnknownProviderError",
    "Vendor",
    "XeroLedgerClient",
    "available_providers",
    "build_client",
    "get_provider_class",
    "register",
]
