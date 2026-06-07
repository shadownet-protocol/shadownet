from __future__ import annotations

from typing import TYPE_CHECKING

from shadownet_x402 import Settings
from shadownet_x402.errors import GateError, PoPError, ShadownetX402Error

if TYPE_CHECKING:
    import pytest


def test_defaults_are_testnet() -> None:
    settings = Settings()
    assert settings.asset_id == 10458941
    assert settings.facilitator_url.startswith("https://")
    assert settings.pay_to is None


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_X402_PRICE_MICRO", "12345")
    assert Settings().price_micro == 12345


def test_error_hierarchy() -> None:
    assert issubclass(PoPError, GateError)
    assert issubclass(GateError, ShadownetX402Error)
