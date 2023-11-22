"""Exception hierarchy for ledger provider integrations.

Every failure that crosses the provider boundary is one of these, so callers
never have to catch ``requests.RequestException`` or a provider-specific type.
"""

from __future__ import annotations


class LedgerError(Exception):
    """Base class for every error raised by a ledger provider."""


class LedgerConfigurationError(LedgerError):
    """A provider was asked to run without the credentials it requires.

    Raised eagerly at construction time so a misconfigured deployment fails
    with a readable message instead of an ``AttributeError`` deep inside a
    vendor SDK.
    """


class LedgerAuthError(LedgerError):
    """The provider rejected our credentials, code, or access token."""


class LedgerNotFound(LedgerError):
    """The requested bill or vendor does not exist in the connected ledger."""


class LedgerTransportError(LedgerError):
    """The provider could not be reached, or answered with an unusable body."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class UnknownProviderError(LedgerError):
    """A provider name was requested that is not in the registry."""
