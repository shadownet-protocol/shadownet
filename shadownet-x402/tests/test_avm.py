"""Tests for real x402-avm settlement.

The on-chain path needs a funded, USDC-opted-in TestNet account, so it runs only
when ``SHADOWNET_X402_TEST_MNEMONIC`` + ``SHADOWNET_X402_TEST_PAY_TO`` are set
(marked ``network``). The rest cover the pure helpers and the fail-closed
contract offline.
"""

from __future__ import annotations

import os

import pytest

from shadownet_x402 import SettlementError, avm
from shadownet_x402.settlement import SettleOutcome


def test_settle_usdc_rejects_nonpositive_amount():
    with pytest.raises(SettlementError):
        avm.settle_usdc(wallet_mnemonic="x", pay_to="PAYEE", amount=0)


@pytest.mark.filterwarnings("ignore")
def test_derive_address_roundtrips_generated_account():
    pytest.importorskip("algosdk")
    from algosdk import account, mnemonic

    secret, address = account.generate_account()
    assert avm.derive_address(mnemonic.from_private_key(secret)) == address


@pytest.mark.filterwarnings("ignore")
def test_derive_address_rejects_bad_mnemonic():
    pytest.importorskip("algosdk")
    with pytest.raises(SettlementError):
        avm.derive_address("not a valid mnemonic")


@pytest.mark.filterwarnings("ignore")
def test_settle_usdc_fails_closed_on_bad_mnemonic():
    pytest.importorskip("x402")
    with pytest.raises(SettlementError):
        avm.settle_usdc(wallet_mnemonic="bogus", pay_to="PAYEE", amount=5000)


@pytest.mark.network
@pytest.mark.filterwarnings("ignore")
def test_settle_usdc_real_testnet_transfer():
    mnemonic_phrase = os.environ.get("SHADOWNET_X402_TEST_MNEMONIC")
    pay_to = os.environ.get("SHADOWNET_X402_TEST_PAY_TO")
    if not mnemonic_phrase or not pay_to:
        pytest.skip("set SHADOWNET_X402_TEST_MNEMONIC + SHADOWNET_X402_TEST_PAY_TO")
    outcome = avm.settle_usdc(wallet_mnemonic=mnemonic_phrase, pay_to=pay_to, amount=1)
    assert isinstance(outcome, SettleOutcome)
    assert outcome.success, outcome.error_reason
    assert outcome.transaction
