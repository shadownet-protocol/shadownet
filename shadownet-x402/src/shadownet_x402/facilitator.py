"""Facilitator adapters: a fake for offline tests, and (Phase 4) the GoPlausible client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shadownet_x402.settlement import SettleOutcome, parse_x_payment

if TYPE_CHECKING:
    from shadownet_x402.requirements import PaymentRequirements


@dataclass(frozen=True, slots=True)
class FakeFacilitator:
    """An in-process facilitator that settles without touching a chain."""

    transaction: str = "STUBTXID"
    success: bool = True

    def settle(self, x_payment: str, requirements: PaymentRequirements) -> SettleOutcome:
        if not self.success:
            return SettleOutcome(False, None, None, requirements.network, "stub settlement failure")
        terms = parse_x_payment(x_payment)
        return SettleOutcome(True, self.transaction, terms.payer, requirements.network)
