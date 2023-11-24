"""Provider-neutral domain types.

QuickBooks calls a payable a ``Bill`` with a ``VendorRef``; Xero calls the same
thing an ``ACCPAY`` invoice with a ``Contact``. Everything above the provider
layer speaks these types instead, so adding a third ledger does not ripple into
the service layer, the views, or the JSON contract.

Money is carried as :class:`decimal.Decimal` and serialised as a string. Binary
floats cannot represent most two-decimal amounts exactly, and silently rounding
a payable is not an acceptable failure mode for an accounts-payable bridge.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Generic, TypeVar

ZERO = Decimal("0.00")


def to_decimal(value: Any) -> Decimal:
    """Coerce a provider-supplied amount to ``Decimal``, defaulting to zero.

    Providers are inconsistent: QuickBooks sends JSON numbers, Xero sends
    strings, and both omit the field entirely when the amount is nil.
    """
    if value is None or value == "":
        return ZERO
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return ZERO


def format_amount(value: Decimal) -> str:
    """Render an amount as an exact decimal string, padded to two places.

    Padding only ever *adds* trailing zeros (``1500`` -> ``"1500.00"``); an
    amount carrying more precision than two places keeps all of it. Nothing is
    rounded away, because rounding a payable is a correctness bug, not a
    formatting choice.
    """
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent > -2:
        value = value.quantize(Decimal("0.01"))
    return str(value)


def to_date(value: Any) -> date | None:
    """Parse an ISO ``YYYY-MM-DD`` date, tolerating None and junk."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class Vendor:
    """A payee: a QuickBooks Vendor or a Xero supplier Contact."""

    provider: str
    id: str
    name: str
    balance: Decimal = ZERO
    currency: str = ""
    email: str = ""
    active: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "id": self.id,
            "name": self.name,
            "balance": format_amount(self.balance),
            "currency": self.currency,
            "email": self.email,
            "active": self.active,
        }


@dataclass(frozen=True, slots=True)
class Bill:
    """A payable owed to a :class:`Vendor`."""

    provider: str
    id: str
    vendor_id: str
    vendor_name: str
    total: Decimal = ZERO
    balance: Decimal = ZERO
    currency: str = ""
    document_number: str = ""
    issued_on: date | None = None
    due_on: date | None = None

    @property
    def is_paid(self) -> bool:
        return self.balance <= ZERO

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "id": self.id,
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor_name,
            "total": format_amount(self.total),
            "balance": format_amount(self.balance),
            "currency": self.currency,
            "document_number": self.document_number,
            "issued_on": self.issued_on.isoformat() if self.issued_on else None,
            "due_on": self.due_on.isoformat() if self.due_on else None,
            "is_paid": self.is_paid,
        }


T = TypeVar("T", Bill, Vendor)


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    """One page of results plus the cursor needed to ask for the next one.

    ``next_offset`` is ``None`` when the provider returned a short page, which
    is how both QuickBooks and Xero signal the end of a collection.
    """

    items: Sequence[T] = field(default_factory=tuple)
    offset: int = 0
    limit: int = 0
    next_offset: int | None = None

    @property
    def has_more(self) -> bool:
        return self.next_offset is not None

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [item.as_dict() for item in self.items],
            "offset": self.offset,
            "limit": self.limit,
            "next_offset": self.next_offset,
            "has_more": self.has_more,
        }
