"""Provider registry — the extension seam.

Adding a third ledger (Sage, NetSuite, FreeAgent) means writing one subclass of
:class:`~api.ledger.base.LedgerClientBase`, decorating it with ``@register``,
and adding its credentials to settings. No existing module changes: not the
service layer, not the views, not the URL conf. That is the whole point of the
abstract base the project started from.

This module deliberately does not import Django. The ledger package is a plain
library that takes a config dict; the Django wiring lives one layer out in
``api.services``. Dependencies point inward.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any, TypeVar

from .base import LedgerClientBase
from .errors import UnknownProviderError
from .oauth import OAuth2Credentials, TokenSet
from .transport import Transport

_REGISTRY: dict[str, type[LedgerClientBase]] = {}

C = TypeVar("C", bound=type[LedgerClientBase])


def register(client_cls: C) -> C:
    """Class decorator that adds a provider under its ``name``."""
    name = getattr(client_cls, "name", "")
    if not name:
        raise ValueError(f"{client_cls.__name__} must define a non-empty `name`")
    _REGISTRY[name] = client_cls
    return client_cls


def available_providers() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def get_provider_class(name: str) -> type[LedgerClientBase]:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownProviderError(
            f"unknown ledger provider {name!r}; known providers: "
            f"{', '.join(available_providers()) or 'none'}"
        ) from None


def build_client(
    name: str,
    config: Mapping[str, Any],
    *,
    transport: Transport,
    token: TokenSet | None = None,
) -> LedgerClientBase:
    """Instantiate a registered provider from a flat config mapping.

    Only the extra keyword arguments a provider actually declares are passed
    through, so one shared settings shape can serve providers with different
    connection parameters.
    """
    client_cls = get_provider_class(name)
    credentials = OAuth2Credentials(
        client_id=str(config.get("client_id") or ""),
        client_secret=str(config.get("client_secret") or ""),
        redirect_uri=str(config.get("redirect_uri") or ""),
    )
    accepted = set(inspect.signature(client_cls.__init__).parameters) - {
        "self", "credentials", "transport", "token",
    }
    extras = {key: config[key] for key in accepted if key in config}
    return client_cls(credentials, transport=transport, token=token, **extras)


# Importing the provider modules is what populates the registry; keep it at the
# bottom so `register` exists by the time they are loaded.
from . import quickbooks as _quickbooks  # noqa: E402
from . import xero as _xero  # noqa: E402

register(_quickbooks.QuickBooksLedgerClient)
register(_xero.XeroLedgerClient)

__all__ = [
    "available_providers",
    "build_client",
    "get_provider_class",
    "register",
]
