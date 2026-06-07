"""Identity-gated x402 payments on Algorand for Shadownet."""

from __future__ import annotations

from shadownet_x402._version import __version__
from shadownet_x402.budget import BudgetStore, InMemoryBudgetStore
from shadownet_x402.client import ShadowX402Client
from shadownet_x402.config import Settings
from shadownet_x402.errors import (
    AmountMismatchError,
    BudgetError,
    GateError,
    PoPError,
    ReplayError,
    SettlementError,
    ShadownetX402Error,
)
from shadownet_x402.facilitator import FakeFacilitator
from shadownet_x402.gate import ShadowIdentity, run_identity_gate
from shadownet_x402.nonce import InMemoryNonceStore, NonceStore
from shadownet_x402.pop import mint_pop, verify_pop
from shadownet_x402.requirements import PaymentRequirements, build_payment_requirements
from shadownet_x402.server import Challenge, Paywall, PaywallResult, Refused, Settled
from shadownet_x402.settlement import (
    Facilitator,
    PaidTerms,
    SettleOutcome,
    encode_x_payment,
    enforce_agreed_equals_paid,
    parse_x_payment,
)

__all__ = [
    "AmountMismatchError",
    "BudgetError",
    "BudgetStore",
    "Challenge",
    "Facilitator",
    "FakeFacilitator",
    "GateError",
    "InMemoryBudgetStore",
    "InMemoryNonceStore",
    "NonceStore",
    "PaidTerms",
    "PaymentRequirements",
    "Paywall",
    "PaywallResult",
    "PoPError",
    "Refused",
    "ReplayError",
    "Settings",
    "SettleOutcome",
    "Settled",
    "SettlementError",
    "ShadowIdentity",
    "ShadowX402Client",
    "ShadownetX402Error",
    "__version__",
    "build_payment_requirements",
    "encode_x_payment",
    "enforce_agreed_equals_paid",
    "mint_pop",
    "parse_x_payment",
    "run_identity_gate",
    "verify_pop",
]
