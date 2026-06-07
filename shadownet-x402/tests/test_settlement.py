from __future__ import annotations

from typing import Any

import pytest

from shadownet_x402.errors import AmountMismatchError
from shadownet_x402.requirements import PaymentRequirements
from shadownet_x402.settlement import (
    PaidTerms,
    encode_x_payment,
    enforce_agreed_equals_paid,
    parse_x_payment,
)

REQUIREMENTS = PaymentRequirements(
    network="algorand:testnet",
    asset="10458941",
    amount=5000,
    pay_to="VENUE",
    resource="https://venue.example/ticket",
)


def _terms(**overrides: Any) -> PaidTerms:
    base: dict[str, Any] = {
        "network": "algorand:testnet",
        "asset": "10458941",
        "amount": 5000,
        "pay_to": "VENUE",
        "payer": "BUYER",
    }
    base.update(overrides)
    return PaidTerms(**base)


def test_roundtrip() -> None:
    terms = _terms()
    assert parse_x_payment(encode_x_payment(terms)) == terms


def test_match_passes() -> None:
    enforce_agreed_equals_paid(_terms(), REQUIREMENTS)


def test_amount_mismatch() -> None:
    with pytest.raises(AmountMismatchError):
        enforce_agreed_equals_paid(_terms(amount=1), REQUIREMENTS)


def test_payto_mismatch() -> None:
    with pytest.raises(AmountMismatchError):
        enforce_agreed_equals_paid(_terms(pay_to="ATTACKER"), REQUIREMENTS)


def test_malformed_payload() -> None:
    with pytest.raises(AmountMismatchError):
        parse_x_payment("%%%not-json%%%")
