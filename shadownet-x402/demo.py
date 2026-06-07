"""Runnable ShadowPay demo: identity-gated x402 — pay, kill switch, agreed=paid."""

from __future__ import annotations

import time

from shadownet.credential import CredentialPayload, RevocationPointer, mint_credential
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.identifiers import encode_public_key

from shadownet_x402.budget import InMemoryBudgetStore
from shadownet_x402.config import Settings
from shadownet_x402.facilitator import FakeFacilitator
from shadownet_x402.nonce import InMemoryNonceStore
from shadownet_x402.pop import mint_pop
from shadownet_x402.server import Paywall, Refused, Settled
from shadownet_x402.settlement import PaidTerms, encode_x_payment

RESOURCE = "https://alice.sh4dow.org/pay"


def banner(text: str) -> None:
    print("\n" + "=" * 68)
    print(text)
    print("=" * 68)


def main() -> None:
    issuer = Ed25519KeyPair.generate()
    issuer_pk = encode_public_key(issuer.public_bytes)
    bob = Ed25519KeyPair.generate()
    bob_sub = encode_public_key(bob.public_bytes)
    now = int(time.time())
    bob_cred = mint_credential(
        CredentialPayload(
            iss="acme.example",
            sub=bob_sub,
            kind="org_affiliation",
            org="acme.example",
            iat=now,
            exp=now + 3600,
            rev=RevocationPointer(epoch="2026q2", idx=0),
        ),
        issuer,
    )

    budget = InMemoryBudgetStore(cap_micro=1_000_000)
    paywall = Paywall(
        Settings(
            pay_to="ALICEALGOADDR",
            price_micro=5000,
            asset_id=10458941,
            network_caip2="algorand:testnet",
        ),
        nonce_store=InMemoryNonceStore(),
        budget_store=budget,
        facilitator=FakeFacilitator(transaction="TESTNET-TXID-9F3A2C"),
        resolve_issuer_key=lambda _iss: issuer_pk,
        check_revoked=lambda _credential: None,
    )

    def pay(*, tamper: bool = False) -> Settled | Refused:
        challenge = paywall.process(resource_url=RESOURCE, credential=bob_cred)
        if isinstance(challenge, Refused):
            return challenge
        pop = mint_pop(bob, sub=bob_sub, audience=RESOURCE, nonce=challenge.nonce)
        req = challenge.requirements
        terms = PaidTerms(
            network=req.network,
            asset=req.asset,
            amount=req.amount,
            pay_to="ATTACKERADDR" if tamper else req.pay_to,
            payer=bob_sub,
        )
        return paywall.process(
            resource_url=RESOURCE, credential=bob_cred, pop=pop, x_payment=encode_x_payment(terms)
        )

    print(f"\npayer Shadow : {bob_sub[:20]}…  (verified org_affiliation @ acme.example)")

    banner("1) A verified Shadow pays — bound to a named identity, not an anonymous wallet")
    result = pay()
    assert isinstance(result, Settled)
    print("  pay 0.005 USDC on Algorand TestNet")
    print(f"  settled -> 200 OK   txid: {result.outcome.transaction}")

    banner("2) Kill switch — revoke the agent and its next payment dies (wallet still funded)")
    budget.revoke(bob_sub)
    result = pay()
    assert isinstance(result, Refused)
    print(f"  revoked the agent -> HTTP {result.status}: {result.reason}")
    print("  the funds are untouched; the identity is the kill switch")
    budget.restore(bob_sub)

    banner("3) Agreed = paid — a tampered payTo is refused before any settlement")
    result = pay(tamper=True)
    assert isinstance(result, Refused)
    print(f"  payTo swapped by a man-in-the-middle -> HTTP {result.status}: {result.reason}")

    print("\nShadowPay: every x402 payment bound to a verified, revocable agent.\n")


if __name__ == "__main__":
    main()
