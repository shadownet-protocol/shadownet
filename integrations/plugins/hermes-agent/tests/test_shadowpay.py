"""Tests for the ShadowPay narrative in demo (stub) mode."""

from __future__ import annotations

import pytest

from shadownet_hermes_plugin import _shadowpay

_LIVE_ENV = (
    "SHADOWNET_X402_WALLET_MNEMONIC",
    "SHADOWNET_X402_PAY_TO",
    "SHADOWNET_X402_AMOUNT",
    "SHADOWNET_X402_ALGOD_URL",
)


@pytest.fixture(autouse=True)
def _clear_live_env(monkeypatch):
    for name in _LIVE_ENV:
        monkeypatch.delenv(name, raising=False)


def test_stub_mode_runs_all_three_beats_with_hint():
    out = _shadowpay.run("alice 0.005")
    assert "SETTLED: txid TESTNET-TXID-9F3A2C (demo, stub settlement)" in out
    assert "REFUSED (403): identity revoked" in out
    assert "REFUSED (402): payment does not match the agreed requirements" in out
    assert "Know Your Agent for x402." in out
    assert "demo mode (stub settlement)" in out


def test_config_hint_present_when_keys_unset():
    assert _shadowpay._config_hint() is not None


def test_config_hint_suppressed_when_live_keys_set(monkeypatch):
    monkeypatch.setenv("SHADOWNET_X402_WALLET_MNEMONIC", "some-mnemonic")
    monkeypatch.setenv("SHADOWNET_X402_PAY_TO", "SOMEPAYEEADDR")
    assert _shadowpay._config_hint() is None
