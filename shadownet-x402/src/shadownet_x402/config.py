"""Typed environment configuration for shadownet-x402."""

from __future__ import annotations

from typing import Final

from pydantic_settings import BaseSettings, SettingsConfigDict

TESTNET_CAIP2: Final = "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="
TESTNET_USDC_ASSET_ID: Final = 10458941
GOPLAUSIBLE_FACILITATOR_URL: Final = "https://facilitator.goplausible.xyz"
DEFAULT_ALGOD_URL: Final = "https://testnet-api.algonode.cloud"


class Settings(BaseSettings):
    """Runtime settings read from ``SHADOWNET_X402_*`` environment variables."""

    model_config = SettingsConfigDict(env_prefix="SHADOWNET_X402_", extra="forbid")

    facilitator_url: str = GOPLAUSIBLE_FACILITATOR_URL
    network_caip2: str = TESTNET_CAIP2
    asset_id: int = TESTNET_USDC_ASSET_ID
    algod_url: str = DEFAULT_ALGOD_URL
    algod_token: str = ""
    pay_to: str | None = None
    price_micro: int = 5_000
    budget_micro: int = 1_000_000
    nonce_ttl_seconds: int = 300
    pop_lifetime_seconds: int = 120
    wallet_mnemonic: str | None = None
    trust_store_path: str | None = None
