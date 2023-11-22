"""HTTP layer.

Every view here does the same four things and nothing else: read query
parameters, call one :class:`~api.services.LedgerService` method, serialise,
return. No provider names, no SDK calls, no business rules. The original views
built a provider client, called four provider methods, and reached into the
returned SDK objects' attributes directly.
"""

from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET

from .ledger import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    LedgerAuthError,
    LedgerConfigurationError,
    LedgerError,
    LedgerNotFound,
    LedgerTransportError,
    UnknownProviderError,
    available_providers,
)
from .services import LedgerService, default_provider
from .tokens import SessionTokenStore

logger = logging.getLogger(__name__)

#: Ledger failures map onto HTTP once, here, instead of in each view.
_STATUS_BY_ERROR: tuple[tuple[type[LedgerError], int], ...] = (
    (LedgerNotFound, 404),
    (LedgerAuthError, 401),
    (UnknownProviderError, 404),
    (LedgerConfigurationError, 503),
    (LedgerTransportError, 502),
)


def _service(request: HttpRequest) -> LedgerService:
    return LedgerService(SessionTokenStore(request.session))


def _status_for(exc: LedgerError) -> int:
    for error_type, status in _STATUS_BY_ERROR:
        if isinstance(exc, error_type):
            return status
    return 500


def _error_response(exc: LedgerError) -> JsonResponse:
    status = _status_for(exc)
    if status >= 500:
        logger.exception("ledger request failed: %s", exc)
    else:
        logger.info("ledger request rejected (%s): %s", status, exc)
    return JsonResponse({"error": type(exc).__name__, "detail": str(exc)}, status=status)


def _paging(request: HttpRequest) -> tuple[int, int]:
    """Read and bound ``limit``/``offset``, ignoring unparseable values."""
    def _int(name: str, default: int) -> int:
        try:
            return int(request.GET.get(name, default))
        except (TypeError, ValueError):
            return default

    limit = max(1, min(_int("limit", DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    offset = max(0, _int("offset", 0))
    return limit, offset


@require_GET
def health(request: HttpRequest) -> JsonResponse:
    """Liveness probe. Never touches a provider, so it stays cheap and honest."""
    return JsonResponse({"status": "ok", "providers": list(available_providers())})


@require_GET
def list_providers(request: HttpRequest) -> JsonResponse:
    store = SessionTokenStore(request.session)
    return JsonResponse(
        {
            "default": default_provider(),
            "providers": [
                {"name": name, "connected": store.get(name) is not None}
                for name in available_providers()
            ],
        }
    )


@require_GET
def initiate_ledger_process(request: HttpRequest) -> HttpResponse:
    """Start the OAuth consent flow and redirect the browser to the provider."""
    provider = request.GET.get("provider") or default_provider()
    try:
        return redirect(_service(request).start_authorization(provider))
    except LedgerError as exc:
        return _error_response(exc)


@require_GET
def ledger_callback(request: HttpRequest, provider: str | None = None) -> JsonResponse:
    """Handle the provider's redirect back: exchange the code, report state."""
    provider = provider or request.GET.get("provider") or default_provider()
    if request.GET.get("error"):
        return JsonResponse(
            {"error": "AuthorizationDenied", "detail": request.GET.get("error")}, status=400
        )
    service = _service(request)
    try:
        service.complete_authorization(
            provider,
            request.GET.get("code", ""),
            request.GET.get("state", ""),
            realm_id=request.GET.get("realmId", ""),
        )
        return JsonResponse(service.connection_summary(provider))
    except LedgerError as exc:
        return _error_response(exc)


@require_GET
def list_bills(request: HttpRequest, provider: str) -> JsonResponse:
    limit, offset = _paging(request)
    try:
        page = _service(request).list_bills(
            provider, limit=limit, offset=offset, vendor_id=request.GET.get("vendor_id")
        )
        return JsonResponse(page.as_dict())
    except LedgerError as exc:
        return _error_response(exc)


@require_GET
def get_bill(request: HttpRequest, provider: str, bill_id: str) -> JsonResponse:
    try:
        return JsonResponse(_service(request).get_bill(provider, bill_id).as_dict())
    except LedgerError as exc:
        return _error_response(exc)


@require_GET
def list_vendors(request: HttpRequest, provider: str) -> JsonResponse:
    limit, offset = _paging(request)
    try:
        page = _service(request).list_vendors(provider, limit=limit, offset=offset)
        return JsonResponse(page.as_dict())
    except LedgerError as exc:
        return _error_response(exc)


@require_GET
def get_vendor(request: HttpRequest, provider: str, vendor_id: str) -> JsonResponse:
    try:
        return JsonResponse(_service(request).get_vendor(provider, vendor_id).as_dict())
    except LedgerError as exc:
        return _error_response(exc)
