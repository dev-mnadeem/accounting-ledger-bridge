"""Where a connected ledger's tokens live between requests.

The original code kept the authorization *code* in the session and rebuilt an
un-authenticated client on every request, so a connection never survived the
callback. Tokens now go through this small interface instead.

The shipped implementation is session-backed, which is right for a single-user
demo and wrong for production: Django's default session store signs the cookie
but does not encrypt it, so a refresh token would travel in the user's browser.
Swapping in a database- or KMS-backed store means implementing
:class:`TokenStore` and pointing ``LedgerService`` at it — no other file moves.
"""

from __future__ import annotations

from typing import Protocol

from .ledger import TokenSet

SESSION_KEY_PREFIX = "ledger_token:"
STATE_KEY_PREFIX = "ledger_state:"


class TokenStore(Protocol):
    """Persist one :class:`TokenSet` per provider."""

    def get(self, provider: str) -> TokenSet | None: ...

    def set(self, provider: str, token: TokenSet) -> None: ...

    def clear(self, provider: str) -> None: ...


class SessionTokenStore:
    """Stores tokens in the Django session of the authorising browser."""

    def __init__(self, session) -> None:
        self._session = session

    def get(self, provider: str) -> TokenSet | None:
        raw = self._session.get(SESSION_KEY_PREFIX + provider)
        return TokenSet.from_dict(raw) if raw else None

    def set(self, provider: str, token: TokenSet) -> None:
        self._session[SESSION_KEY_PREFIX + provider] = token.as_dict()
        self._session.modified = True

    def clear(self, provider: str) -> None:
        self._session.pop(SESSION_KEY_PREFIX + provider, None)
        self._session.modified = True

    # -- CSRF state for the OAuth round trip ---------------------------

    def put_state(self, provider: str, state: str) -> None:
        self._session[STATE_KEY_PREFIX + provider] = state
        self._session.modified = True

    def pop_state(self, provider: str) -> str:
        state = self._session.pop(STATE_KEY_PREFIX + provider, "")
        self._session.modified = True
        return state


class InMemoryTokenStore:
    """A dict-backed store, used by the tests and by management commands."""

    def __init__(self) -> None:
        self._tokens: dict[str, TokenSet] = {}
        self._states: dict[str, str] = {}

    def get(self, provider: str) -> TokenSet | None:
        return self._tokens.get(provider)

    def set(self, provider: str, token: TokenSet) -> None:
        self._tokens[provider] = token

    def clear(self, provider: str) -> None:
        self._tokens.pop(provider, None)

    def put_state(self, provider: str, state: str) -> None:
        self._states[provider] = state

    def pop_state(self, provider: str) -> str:
        return self._states.pop(provider, "")
