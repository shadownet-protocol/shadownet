"""Real Algorand TestNet USDC settlement for ShadowPay via the x402-avm SDK.

Self-contained: depends only on the ``x402`` (x402-avm) and ``algosdk``
(py-algorand-sdk) PyPI packages declared in this plugin's dependencies, so it
ships in the wheel and runs on a remote Hermes with no extra package. The x402
facilitator runs in-process against AlgoNode TestNet algod (no external
facilitator service); each call settles a single ``exact`` USDC ASA transfer.

Imports are deferred so the module loads even where these deps are absent, in
which case settlement raises :class:`SettlementError` and the caller falls back
to the demo stub.
"""

from __future__ import annotations

import base64
from decimal import Decimal, InvalidOperation
from typing import Any

USDC_DECIMALS = 6
TESTNET_CAIP2 = "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="
TESTNET_USDC_ASA_ID = 10458941
DEFAULT_ALGOD_URL = "https://testnet-api.algonode.cloud"


class SettlementError(RuntimeError):
    """Raised when a real on-chain settlement cannot be completed."""


def to_atomic(amount_usdc: str) -> int:
    """Convert a human USDC amount (``"0.005"``) to atomic units (6 decimals)."""
    try:
        value = Decimal(str(amount_usdc))
    except (InvalidOperation, ValueError) as exc:
        raise SettlementError(f"invalid USDC amount: {amount_usdc!r}") from exc
    if value <= 0:
        raise SettlementError(f"USDC amount must be positive: {amount_usdc!r}")
    return int(value * (10**USDC_DECIMALS))


def payer_address(wallet_mnemonic: str) -> str:
    """Derive the Algorand address for a 25-word wallet mnemonic."""
    from algosdk import account, mnemonic

    try:
        secret = mnemonic.to_private_key(wallet_mnemonic.strip())
    except Exception as exc:
        raise SettlementError(f"invalid wallet mnemonic: {exc}") from exc
    return str(account.address_from_private_key(secret))


class _ClientSigner:
    """x402-avm ``ClientAvmSigner`` backed by the payer's mnemonic."""

    def __init__(self, wallet_mnemonic: str) -> None:
        from algosdk import account, mnemonic

        try:
            self._secret = mnemonic.to_private_key(wallet_mnemonic.strip())
        except Exception as exc:
            raise SettlementError(f"invalid wallet mnemonic: {exc}") from exc
        self._address = str(account.address_from_private_key(self._secret))

    @property
    def address(self) -> str:
        return self._address

    def sign_transactions(
        self, unsigned_txns: list[bytes], indexes_to_sign: list[int]
    ) -> list[bytes | None]:
        from algosdk import encoding

        signed: list[bytes | None] = []
        for index, raw in enumerate(unsigned_txns):
            if index in indexes_to_sign:
                txn = encoding.msgpack_decode(base64.b64encode(raw).decode())
                signed.append(base64.b64decode(encoding.msgpack_encode(txn.sign(self._secret))))
            else:
                signed.append(None)
        return signed


class _RelayFacilitatorSigner:
    """x402-avm ``FacilitatorAvmSigner`` that only relays to algod.

    Normal (non-gasless) mode: the payer covers its own transaction fee, so the
    facilitator manages no fee-payer accounts (``get_addresses`` is empty) and
    merely simulates, submits, and confirms the buyer-signed group.
    """

    def __init__(self, algod_url: str) -> None:
        from algosdk.v2client import algod

        self._algod = algod.AlgodClient("", algod_url)

    def get_addresses(self) -> list[str]:
        return []

    def sign_transaction(self, txn_bytes: bytes, fee_payer: str, network: str) -> bytes:
        raise SettlementError("facilitator manages no fee-payer accounts")

    def sign_group(
        self, group_bytes: list[bytes], fee_payer: str, indexes_to_sign: list[int], network: str
    ) -> list[bytes]:
        raise SettlementError("facilitator manages no fee-payer accounts")

    def _decode(self, group_bytes: list[bytes]) -> list[Any]:
        from algosdk import encoding

        return [encoding.msgpack_decode(base64.b64encode(b).decode()) for b in group_bytes]

    def simulate_group(self, group_bytes: list[bytes], network: str) -> None:
        result: Any = self._algod.simulate_raw_transactions(self._decode(group_bytes))
        groups = result.get("txn-groups") if isinstance(result, dict) else None
        if groups:
            failure = groups[0].get("failure-message")
            if failure:
                raise SettlementError(f"simulation failed: {failure}")

    def send_group(self, group_bytes: list[bytes], network: str) -> str:
        return str(self._algod.send_transactions(self._decode(group_bytes)))

    def confirm_transaction(self, txid: str, network: str, rounds: int = 4) -> None:
        from algosdk import transaction

        transaction.wait_for_confirmation(self._algod, txid, rounds)


def settle_usdc(
    *,
    wallet_mnemonic: str,
    pay_to: str,
    amount_usdc: str,
    network: str | None = None,
    algod_url: str | None = None,
) -> str:
    """Settle a real USDC transfer on Algorand TestNet; return the transaction id.

    Builds an x402 ``exact`` payment from ``wallet_mnemonic`` to ``pay_to`` for
    ``amount_usdc`` and settles it through an in-process facilitator. Raises
    :class:`SettlementError` on any failure (missing deps, bad mnemonic,
    unfunded or not-opted-in accounts, network error).
    """
    try:
        from x402 import (
            PaymentRequired,
            PaymentRequirements,
            ResourceInfo,
            x402ClientSync,
            x402FacilitatorSync,
        )
        from x402.mechanisms.avm.exact import (
            register_exact_avm_client,
            register_exact_avm_facilitator,
        )
    except ImportError as exc:
        raise SettlementError(
            "x402-avm and py-algorand-sdk are required for live settlement"
        ) from exc

    net = network or TESTNET_CAIP2
    url = algod_url or DEFAULT_ALGOD_URL
    amount = to_atomic(amount_usdc)

    client = x402ClientSync()
    register_exact_avm_client(client, _ClientSigner(wallet_mnemonic), networks=net, algod_url=url)

    facilitator = x402FacilitatorSync()
    register_exact_avm_facilitator(facilitator, _RelayFacilitatorSigner(url), networks=net)

    requirements = PaymentRequirements(
        scheme="exact",
        network=net,
        asset=str(TESTNET_USDC_ASA_ID),
        amount=str(amount),
        pay_to=pay_to,
        max_timeout_seconds=120,
    )
    payment_required = PaymentRequired(
        accepts=[requirements],
        resource=ResourceInfo(url="shadowpay://settle"),
    )

    try:
        payload = client.create_payment_payload(payment_required)
    except Exception as exc:
        raise SettlementError(f"failed to build payment: {exc}") from exc

    outcome: Any = facilitator.settle(payload, requirements)
    if not getattr(outcome, "success", False):
        reason = getattr(outcome, "error_reason", None) or "unknown"
        message = getattr(outcome, "error_message", None)
        suffix = f" ({message})" if message else ""
        raise SettlementError(f"on-chain settlement refused: {reason}{suffix}")
    txid = str(getattr(outcome, "transaction", "") or "")
    if not txid:
        raise SettlementError("settlement returned no transaction id")
    return txid
