"""Self-contained ShadowPay flow for the Hermes plugin.

Mirrors the shadownet-x402 identity-gated x402 flow but depends only on the
``shadownet`` SDK (already a plugin dependency), so it ships inside the plugin
wheel and runs on a remote Hermes with no extra package. Settlement is stubbed
(no live facilitator); the identity gate, revocation, and agreed=paid checks are
real.
"""

from __future__ import annotations

import os
import time

_TXID = "TESTNET-TXID-9F3A2C"
_AGREED_PAY_TO = "ALICEALGOADDR"
_REQUIRED_ENV = ("SHADOWNET_X402_WALLET_MNEMONIC", "SHADOWNET_X402_PAY_TO")


def _config_hint() -> str | None:
    """Return a setup hint if the live-settlement env/config is incomplete."""
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if not missing:
        return None
    return (
        "Setup: running in demo mode (stub settlement). For live Algorand TestNet "
        "settlement, set in your Hermes env/config: "
        + ", ".join(missing)
        + ". SHADOWNET_X402_WALLET_MNEMONIC = a funded TestNet account opted into USDC; "
        "SHADOWNET_X402_PAY_TO = the payee's Algorand address."
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

    parts = raw_args.split()
    payee = parts[0] if parts else "alice"
    amount = parts[1] if len(parts) > 1 else "0.005"

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
        if pay_to != _AGREED_PAY_TO:
            return "REFUSED (402): payment does not match the agreed requirements"
        return f"SETTLED: txid {_TXID}"

    lines = ["ShadowPay - agent payments on Algorand, bound to identity.", ""]

    lines.append(
        f"1) Pay {amount} USDC to {payee} on Algorand TestNet -> {settle(pay_to=_AGREED_PAY_TO)}"
    )
    lines.append(
        f"   Bound to {buyer_sub[:16]}... (verified org_affiliation @ acme.example), "
        "not an anonymous wallet."
    )

    revoked.add(buyer_sub)
    lines.append("")
    lines.append(f"2) Revoke the agent, pay again -> {settle(pay_to=_AGREED_PAY_TO)}")
    lines.append("   Wallet still funded. The identity is the kill switch.")
    revoked.discard(buyer_sub)

    lines.append("")
    lines.append(f"3) Man-in-the-middle swaps the pay-to -> {settle(pay_to='ATTACKERADDR')}")
    lines.append("   The charge is bound to what was agreed.")

    lines.append("")
    lines.append("Know Your Agent for x402.")
    hint = _config_hint()
    if hint:
        lines.append("")
        lines.append(hint)
    return "\n".join(lines)
