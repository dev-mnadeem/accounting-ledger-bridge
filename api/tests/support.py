"""Test doubles and the network guard.

Two rules this module enforces for the whole suite:

1. **No test may open a socket.** :class:`NoNetworkMixin` replaces
   ``socket.socket.connect`` with a failing stub, so a regression that
   reintroduces a live call to Intuit or Xero fails the suite instead of
   quietly making a paid API request. The original suite had no such guard, and
   the code under test made a live HTTPS request from a constructor.
2. **Providers talk to a recorded transport.** :class:`FakeTransport` returns
   canned responses and records every request, so assertions can be made about
   the exact URL, query and headers that would have gone out.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from api.ledger import OAuth2Credentials, Response, TokenSet

CREDENTIALS = OAuth2Credentials(
    client_id="test-client-id",
    client_secret="test-client-secret",
    redirect_uri="http://localhost:8870/ledger/quickbooks/callback/",
)


def live_token(**overrides) -> TokenSet:
    data = {
        "access_token": "test-access-token",
        "refresh_token": "test-refresh-token",
        "expires_at": 9_999_999_999.0,
        "tenant_id": "test-tenant",
    }
    data.update(overrides)
    return TokenSet(**data)


@dataclass
class RecordedRequest:
    method: str
    url: str
    params: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    auth: tuple[str, str] | None = None


Handler = Callable[[RecordedRequest], Response]


class FakeTransport:
    """A :class:`~api.ledger.Transport` that answers from a routing table.

    Routes are matched on a substring of the URL, in insertion order. An
    unmatched request raises, so a test can never silently pass because a call
    it did not expect returned ``None``.
    """

    def __init__(self, routes: Mapping[str, Response | Handler] | None = None) -> None:
        self.routes: dict[str, Response | Handler] = dict(routes or {})
        self.requests: list[RecordedRequest] = []

    def route(self, url_fragment: str, response: Response | Handler) -> FakeTransport:
        self.routes[url_fragment] = response
        return self

    def request(self, method, url, *, params=None, data=None, headers=None, auth=None):
        recorded = RecordedRequest(
            method=method,
            url=url,
            params=dict(params or {}),
            data=dict(data or {}),
            headers=dict(headers or {}),
            auth=auth,
        )
        self.requests.append(recorded)
        for fragment, response in self.routes.items():
            if fragment in url:
                return response(recorded) if callable(response) else response
        raise AssertionError(
            f"FakeTransport received an unrouted request: {method} {url}\n"
            f"known routes: {list(self.routes)}"
        )

    # -- assertions ----------------------------------------------------

    @property
    def last(self) -> RecordedRequest:
        assert self.requests, "no request was made"
        return self.requests[-1]

    def urls(self) -> list[str]:
        return [r.url for r in self.requests]


def json_response(payload: Any, status_code: int = 200) -> Response:
    return Response(status_code=status_code, json=payload, text=str(payload))


class NoNetworkMixin:
    """Fails any test that tries to open a TCP connection."""

    def setUp(self) -> None:  # noqa: N802 - unittest API
        super().setUp()
        self._real_connect = socket.socket.connect
        self._real_connect_ex = socket.socket.connect_ex

        def _blocked(_self, address, *args, **kwargs):
            raise AssertionError(
                f"This test attempted a real network connection to {address!r}. "
                f"Every outbound call must go through a FakeTransport."
            )

        socket.socket.connect = _blocked
        socket.socket.connect_ex = _blocked
        self.addCleanup(self._restore_network)

    def _restore_network(self) -> None:
        socket.socket.connect = self._real_connect
        socket.socket.connect_ex = self._real_connect_ex
