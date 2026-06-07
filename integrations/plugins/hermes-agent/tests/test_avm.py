"""Offline tests for the real-settlement helpers in ``_avm``.

The on-chain path needs a funded TestNet account, so it is exercised manually,
not here. These cover the pure helpers and the fail-closed error contract.
"""

from __future__ import annotations

import pytest

from shadownet_hermes_plugin import _avm


def test_to_atomic_converts_usdc_to_micro():
    assert _avm.to_atomic("0.005") == 5000
    assert _avm.to_atomic("1") == 1_000_000
    assert _avm.to_atomic("0.000001") == 1


@pytest.mark.parametrize("bad", ["0", "-1", "abc", ""])
def test_to_atomic_rejects_nonpositive_or_garbage(bad):
    with pytest.raises(_avm.SettlementError):
        _avm.to_atomic(bad)


def test_payer_address_roundtrips_a_generated_account():
    pytest.importorskip("algosdk")
    from algosdk import account, mnemonic

    secret, address = account.generate_account()
    phrase = mnemonic.from_private_key(secret)
    assert _avm.payer_address(phrase) == address


def test_payer_address_rejects_bad_mnemonic():
    pytest.importorskip("algosdk")
    with pytest.raises(_avm.SettlementError):
        _avm.payer_address("not a valid mnemonic phrase")


def test_settle_usdc_fails_closed_on_bad_input():
    # Whether or not the optional deps are installed, a bad mnemonic must surface
    # as a SettlementError, never a raw exception leaking to the caller.
    with pytest.raises(_avm.SettlementError):
        _avm.settle_usdc(wallet_mnemonic="bogus", pay_to="PAYEE", amount_usdc="0.005")
