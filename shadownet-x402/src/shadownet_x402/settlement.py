"""Parse the X-PAYMENT payload, enforce agreed=paid, and settle via a facilitator."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from shadownet_x402.errors import AmountMismatchError

if TYPE_CHECKING:
    from shadownet_x402.requirements import PaymentRequirements


@dataclass(frozen=True, slots=True)
class PaidTerms:
    """The asset, amount, and payee a buyer actually signed into an X-PAYMENT."""

    network: str
    asset: str
    amount: int
    pay_to: str
    payer: str | None = None


@dataclass(frozen=True, slots=True)
class SettleOutcome:
    """The result of a facilitator settlement."""

    success: bool
    transaction: str | None
    payer: str | None
    network: str
    error_reason: str | None = None


class Facilitator(Protocol):
    def settle(self, x_payment: str, requirements: PaymentRequirements) -> SettleOutcome: ...


def encode_x_payment(terms: PaidTerms) -> str:
    """Encode paid terms as a base64url X-PAYMENT value (the stub wire format)."""
    payload = {
        "network": terms.network,
        "asset": terms.asset,
        "amount": terms.amount,
        "payTo": terms.pay_to,
        "payer": terms.payer,
    }
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def parse_x_payment(x_payment: str) -> PaidTerms:
    """Decode an X-PAYMENT value into the terms the buyer signed."""
    try:
        raw = base64.urlsafe_b64decode(x_payment + "=" * (-len(x_payment) % 4))
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise AmountMismatchError(f"malformed X-PAYMENT: {exc}") from exc
    try:
        return PaidTerms(
            network=str(data["network"]),
            asset=str(data["asset"]),
            amount=int(data["amount"]),
            pay_to=str(data["payTo"]),
            payer=data.get("payer"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AmountMismatchError(f"X-PAYMENT missing required fields: {exc}") from exc


def enforce_agreed_equals_paid(terms: PaidTerms, requirements: PaymentRequirements) -> None:
    """Raise AmountMismatchError unless the signed terms match the quoted requirements."""
    quoted = (requirements.network, requirements.asset, requirements.amount, requirements.pay_to)
    if (terms.network, terms.asset, terms.amount, terms.pay_to) != quoted:
        raise AmountMismatchError("signed payment does not match the agreed requirements")
