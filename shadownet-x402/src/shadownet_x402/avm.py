"""Real Algorand USDC settlement via the x402-avm SDK.

Per this package's CLAUDE.md, chain signing and facilitator settlement live in
``x402-avm``; this module only adapts a mnemonic-backed wallet and an in-process
facilitator into the package's :class:`SettleOutcome`. It drives the x402
``exact`` flow end-to-end against AlgoNode algod (no external facilitator
service), settling a single USDC ASA transfer.

Requires the optional ``algorand`` extra (``x402-avm`` + ``py-algorand-sdk``);
those imports are deferred so the core package stays importable without them, and
any configuration failure surfaces as :class:`SettlementError`.
"""

from __future__ import annotations

import base64
from typing import Any

from shadownet_x402.config import DEFAULT_ALGOD_URL, TESTNET_CAIP2, TESTNET_USDC_ASSET_ID
from shadownet_x402.errors import SettlementError
from shadownet_x402.settlement import SettleOutcome


def derive_address(wallet_mnemonic: str) -> str:
    """Return the Algorand address for a 25-word wallet mnemonic."""
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
    """x402-avm ``FacilitatorAvmSigner`` that relays a buyer-signed group to algod.

    The payer covers its own fee, so the facilitator manages no fee-payer
    accounts and only simulates, submits, and confirms the group.
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
    amount: int,
    network: str = TESTNET_CAIP2,
    asset_id: int = TESTNET_USDC_ASSET_ID,
    algod_url: str = DEFAULT_ALGOD_URL,
) -> SettleOutcome:
    """Settle a real USDC transfer of ``amount`` atomic units; return the outcome.

    Drives the x402 ``exact`` flow (buyer signs, in-process facilitator submits)
    and maps the result to :class:`SettleOutcome`. Raises :class:`SettlementError`
    on configuration or build failures; on-chain refusals are returned as a
    non-success outcome.
    """
    if amount <= 0:
        raise SettlementError(f"amount must be positive: {amount}")
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
            "the 'algorand' extra (x402-avm + py-algorand-sdk) is required for settlement"
        ) from exc

    signer = _ClientSigner(wallet_mnemonic)
    client = x402ClientSync()
    register_exact_avm_client(client, signer, networks=network, algod_url=algod_url)

    facilitator = x402FacilitatorSync()
    register_exact_avm_facilitator(
        facilitator, _RelayFacilitatorSigner(algod_url), networks=network
    )

    requirements = PaymentRequirements(
        scheme="exact",
        network=network,
        asset=str(asset_id),
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
    return SettleOutcome(
        success=bool(getattr(outcome, "success", False)),
        transaction=getattr(outcome, "transaction", None) or None,
        payer=getattr(outcome, "payer", None) or signer.address,
        network=network,
        error_reason=getattr(outcome, "error_reason", None),
    )
