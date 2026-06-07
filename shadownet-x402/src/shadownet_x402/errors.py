"""Exception hierarchy for shadownet-x402."""

from __future__ import annotations

from shadownet.errors import ShadownetError

__all__ = [
    "AmountMismatchError",
    "BudgetError",
    "GateError",
    "PoPError",
    "ReplayError",
    "SettlementError",
    "ShadownetX402Error",
]


class ShadownetX402Error(ShadownetError):
    """Root of the shadownet-x402 exception hierarchy."""


class GateError(ShadownetX402Error):
    """The identity gate refused the request."""


class PoPError(GateError):
    """A proof-of-possession failed to verify."""


class ReplayError(ShadownetX402Error):
    """A payment nonce was reused, expired, or bound to a different identity."""


class BudgetError(ShadownetX402Error):
    """The payment would exceed the per-identity budget, or the identity is revoked."""


class AmountMismatchError(ShadownetX402Error):
    """The signed payment does not match the agreed payment requirements."""


class SettlementError(ShadownetX402Error):
    """The facilitator failed to verify or settle the payment."""
