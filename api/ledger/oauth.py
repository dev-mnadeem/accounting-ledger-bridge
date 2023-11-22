"""The OAuth 2.0 authorization-code flow, shared by every provider.

QuickBooks and Xero implement the same RFC 6749 flow behind different URLs, so
the flow lives here once and each provider supplies its endpoints. Nothing in
this module touches the network at import or construction time: building a
client is free, and the first byte leaves the process only when a caller
explicitly asks to exchange or refresh a token.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from .errors import LedgerAuthError, LedgerConfigurationError
from .transport import Transport, raise_for_status

#: Refresh this many seconds before the provider's stated expiry, so a token
#: cannot lapse mid-flight between our check and the provider's clock.
EXPIRY_SKEW_SECONDS = 60


@dataclass(frozen=True, slots=True)
class OAuth2Config:
    """Static, per-provider OAuth 2.0 endpoints and scopes."""

    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OAuth2Credentials:
    """The per-deployment half of the configuration: id, secret, redirect URI."""

    client_id: str
    client_secret: str
    redirect_uri: str

    def require(self, provider: str) -> None:
        """Fail fast, and name every missing field at once."""
        missing = [
            name
            for name in ("client_id", "client_secret", "redirect_uri")
            if not getattr(self, name)
        ]
        if missing:
            raise LedgerConfigurationError(
                f"{provider}: missing required credentials: {', '.join(missing)}. "
                f"Set the matching environment variables (see example.env)."
            )


@dataclass(slots=True)
class TokenSet:
    """An access/refresh token pair with an absolute expiry."""

    access_token: str
    refresh_token: str = ""
    expires_at: float = 0.0
    tenant_id: str = ""

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        tenant_id: str = "",
        now: float | None = None,
    ) -> TokenSet:
        access_token = payload.get("access_token")
        if not access_token:
            raise LedgerAuthError("token endpoint returned no access_token")
        issued_at = time.time() if now is None else now
        expires_in = float(payload.get("expires_in") or 0)
        return cls(
            access_token=str(access_token),
            refresh_token=str(payload.get("refresh_token") or ""),
            expires_at=issued_at + expires_in,
            tenant_id=tenant_id,
        )

    def is_expired(self, *, now: float | None = None) -> bool:
        if not self.expires_at:
            return False
        current = time.time() if now is None else now
        return current >= self.expires_at - EXPIRY_SKEW_SECONDS

    def as_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "tenant_id": self.tenant_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TokenSet:
        return cls(
            access_token=str(data.get("access_token") or ""),
            refresh_token=str(data.get("refresh_token") or ""),
            expires_at=float(data.get("expires_at") or 0.0),
            tenant_id=str(data.get("tenant_id") or ""),
        )


@dataclass
class OAuth2Flow:
    """Executes the authorization-code grant against one provider."""

    config: OAuth2Config
    credentials: OAuth2Credentials
    transport: Transport
    provider: str = "ledger"
    _state: str = field(default="", init=False, repr=False)

    def authorization_url(self, state: str | None = None) -> tuple[str, str]:
        """Build the consent URL. Returns ``(url, state)``.

        The CSRF ``state`` is generated here and returned to the caller so it
        can be stored and compared on the callback. The original implementation
        omitted ``state`` entirely, which left the callback open to forgery.
        """
        self.credentials.require(self.provider)
        state = state or secrets.token_urlsafe(24)
        self._state = state
        query = urlencode(
            {
                "client_id": self.credentials.client_id,
                "response_type": "code",
                "scope": " ".join(self.config.scopes),
                "redirect_uri": self.credentials.redirect_uri,
                "state": state,
            }
        )
        return f"{self.config.authorize_url}?{query}", state

    def exchange_code(self, code: str) -> TokenSet:
        if not code:
            raise LedgerAuthError(f"{self.provider}: callback carried no authorization code")
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.credentials.redirect_uri,
            }
        )

    def refresh(self, token: TokenSet) -> TokenSet:
        if not token.refresh_token:
            raise LedgerAuthError(f"{self.provider}: no refresh token available")
        refreshed = self._token_request(
            {"grant_type": "refresh_token", "refresh_token": token.refresh_token}
        )
        # Providers may omit the refresh token on rotation; keep the old one.
        if not refreshed.refresh_token:
            refreshed.refresh_token = token.refresh_token
        refreshed.tenant_id = refreshed.tenant_id or token.tenant_id
        return refreshed

    def _token_request(self, data: dict[str, str]) -> TokenSet:
        self.credentials.require(self.provider)
        response = self.transport.request(
            "POST",
            self.config.token_url,
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            auth=(self.credentials.client_id, self.credentials.client_secret),
        )
        raise_for_status(response, context=f"{self.provider} token exchange")
        if not isinstance(response.json, Mapping):
            raise LedgerAuthError(f"{self.provider}: token endpoint returned a non-JSON body")
        return TokenSet.from_payload(response.json)
