"""Self-contained ShadowPay flow for the Hermes plugin.

Mints and verifies a real shadownet credential, then runs the pay / revoke /
tamper beats. The identity gate, revocation, and agreed=paid checks are always
real. When live Algorand keys are configured (``SHADOWNET_X402_WALLET_MNEMONIC``
+ ``SHADOWNET_X402_PAY_TO``), the successful beat settles a real USDC transfer on
Algorand TestNet via x402 (see :mod:`._avm`); otherwise it runs in demo mode with
a stubbed settlement.
"""

from __future__ import annotations

import os
import time

from shadownet_hermes_plugin import _avm

_STUB_TXID = "TESTNET-TXID-9F3A2C"
_STUB_PAY_TO = "ALICEALGOADDR"
_TAMPERED_PAY_TO = "TAMPEREDPAYTOADDR"
_DEFAULT_AMOUNT = "0.005"
_LORA_TX_URL = "https://lora.algokit.io/testnet/tx/"
_REQUIRED_ENV = ("SHADOWNET_X402_WALLET_MNEMONIC", "SHADOWNET_X402_PAY_TO")


def _config_hint() -> str | None:
    """Return a setup hint if the live-settlement env/config is incomplete."""
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if not missing:
        return None
    return (
        "Setup: running in demo mode (stub settlement). For a real Algorand TestNet "
        "USDC transfer, set in your Hermes env/config: " + ", ".join(missing) + ". "
        "SHADOWNET_X402_WALLET_MNEMONIC = a funded 25-word TestNet account opted into "
        "USDC (ASA 10458941); SHADOWNET_X402_PAY_TO = the payee address (also opted in)."
    )


def run(raw_args: str = "") -> str:
    """Run the pay / revoke / tamper beats and return a chat-ready narrative."""
    from shadownet.credential import (
        CredentialError,
        CredentialPayload,
        RevocationPointer,
        mint_credential,
        verify_credential,
    )
    from shadownet.crypto.ed25519 import Ed25519KeyPair
    from shadownet.identifiers import encode_public_key

    wallet_mnemonic = os.environ.get("SHADOWNET_X402_WALLET_MNEMONIC", "").strip()
    configured_pay_to = os.environ.get("SHADOWNET_X402_PAY_TO", "").strip()
    algod_url = os.environ.get("SHADOWNET_X402_ALGOD_URL", "").strip() or None
    live = bool(wallet_mnemonic and configured_pay_to)
    agreed_pay_to = configured_pay_to if live else _STUB_PAY_TO

    parts = raw_args.split()
    payee = parts[0] if parts else "alice"
    if len(parts) > 1:
        amount = parts[1]
    else:
        amount = os.environ.get("SHADOWNET_X402_AMOUNT", _DEFAULT_AMOUNT) or _DEFAULT_AMOUNT

    issuer = Ed25519KeyPair.generate()
    issuer_pk = encode_public_key(issuer.public_bytes)
    buyer = Ed25519KeyPair.generate()
    buyer_sub = encode_public_key(buyer.public_bytes)
    now = int(time.time())
    credential = mint_credential(
        CredentialPayload(
            iss="acme.example",
            sub=buyer_sub,
            kind="org_affiliation",
            org="acme.example",
            iat=now,
            exp=now + 3600,
            rev=RevocationPointer(epoch="2026q2", idx=0),
        ),
        issuer,
    )
    revoked: set[str] = set()

    def settle(*, pay_to: str) -> str:
        try:
            verify_credential(credential, resolve_issuer_key=lambda _iss: issuer_pk)
        except CredentialError:
            return "REFUSED (401): identity not verified"
        if buyer_sub in revoked:
            return "REFUSED (403): identity revoked"
        if pay_to != agreed_pay_to:
            return "REFUSED (402): payment does not match the agreed requirements"
        if not live:
            return f"SETTLED: txid {_STUB_TXID} (demo, stub settlement)"
        try:
            txid = _avm.settle_usdc(
                wallet_mnemonic=wallet_mnemonic,
                pay_to=agreed_pay_to,
                amount_usdc=amount,
                algod_url=algod_url,
            )
        except _avm.SettlementError as exc:
            return f"REFUSED: on-chain settlement failed ({exc})"
        return f"SETTLED: {txid}\n   {_LORA_TX_URL}{txid}"

    network_label = "Algorand TestNet" if live else "Algorand TestNet (demo)"
    lines = ["ShadowPay - agent payments on Algorand, bound to identity.", ""]
    lines.append(
        f"1) Pay {amount} USDC to {payee} on {network_label} -> {settle(pay_to=agreed_pay_to)}"
    )
    lines.append(
        f"   Bound to {buyer_sub[:16]}... (verified org_affiliation @ acme.example), "
        "not an anonymous wallet."
    )

    revoked.add(buyer_sub)
    lines.append("")
    lines.append(f"2) Revoke the agent, pay again -> {settle(pay_to=agreed_pay_to)}")
    lines.append("   Wallet still funded. The identity is the kill switch.")
    revoked.discard(buyer_sub)

    lines.append("")
    lines.append(f"3) Man-in-the-middle swaps the pay-to -> {settle(pay_to=_TAMPERED_PAY_TO)}")
    lines.append("   The charge is bound to what was agreed.")

    lines.append("")
    lines.append("Know Your Agent for x402.")
    hint = _config_hint()
    if hint:
        lines.append("")
        lines.append(hint)
    return "\n".join(lines)
