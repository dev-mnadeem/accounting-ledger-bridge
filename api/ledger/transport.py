"""The single seam through which every provider reaches the network.

Providers never import ``requests`` directly. They are handed a
:class:`Transport` and call it; tests hand them a fake. That is what lets the
whole suite run with sockets blocked, and it is the only place retry, timeout
and logging policy has to live.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .errors import LedgerAuthError, LedgerNotFound, LedgerTransportError

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
#: Ledger APIs rate-limit aggressively; retry the idempotent reads we issue.
RETRY_STATUSES = (429, 500, 502, 503, 504)


@dataclass(frozen=True, slots=True)
class Response:
    """The provider-neutral subset of an HTTP response we actually use."""

    status_code: int
    json: Any = None
    text: str = ""


class Transport(Protocol):
    """Anything that can perform an HTTP request and return a :class:`Response`."""

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        auth: tuple[str, str] | None = None,
    ) -> Response: ...


@dataclass
class RequestsTransport:
    """Production transport: a pooled ``requests`` session with bounded retries."""

    timeout: float = DEFAULT_TIMEOUT
    total_retries: int = 3
    _session: requests.Session = field(default_factory=requests.Session, repr=False)

    def __post_init__(self) -> None:
        retry = Retry(
            total=self.total_retries,
            backoff_factor=0.5,
            status_forcelist=RETRY_STATUSES,
            allowed_methods=frozenset({"GET", "POST"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=20)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        auth: tuple[str, str] | None = None,
    ) -> Response:
        try:
            raw = self._session.request(
                method,
                url,
                params=dict(params or {}),
                data=dict(data or {}),
                headers=dict(headers or {}),
                auth=auth,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise LedgerTransportError(f"{method} {url} failed: {exc}") from exc

        try:
            payload = raw.json()
        except ValueError:
            payload = None
        return Response(status_code=raw.status_code, json=payload, text=raw.text)

    def close(self) -> None:
        self._session.close()


def raise_for_status(response: Response, *, context: str) -> Response:
    """Translate an HTTP status into the ledger exception hierarchy."""
    if 200 <= response.status_code < 300:
        return response
    if response.status_code in (401, 403):
        raise LedgerAuthError(f"{context}: provider rejected the credentials "
                              f"(HTTP {response.status_code})")
    if response.status_code == 404:
        raise LedgerNotFound(f"{context}: not found")
    logger.warning("%s: provider returned HTTP %s", context, response.status_code)
    raise LedgerTransportError(
        f"{context}: provider returned HTTP {response.status_code}",
        status_code=response.status_code,
    )
