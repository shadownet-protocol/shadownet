"""ShadowPay demo posted live into a Telegram chat — shadow-to-shadow x402 payments.

Run:
    export TELEGRAM_BOT_TOKEN=<your Hermes bot token>
    export TELEGRAM_CHAT_ID=<your chat id>
    uv run python demo_telegram.py

Uses the Telegram sendMessage API only, so it runs fine alongside a live Hermes
bot. If TELEGRAM_CHAT_ID is unset it tries getUpdates (which conflicts with a
polling Hermes — prefer setting the chat id).
"""

from __future__ import annotations

import os
import time

import httpx
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
API = "https://api.telegram.org"


def resolve_chat_id(token: str) -> str:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if chat_id:
        return chat_id
    data = httpx.get(f"{API}/bot{token}/getUpdates", timeout=10).json()
    results = data.get("result", [])
    if not results:
        raise SystemExit("No chat found — send any message to your bot, then re-run.")
    msg = results[-1].get("message") or results[-1].get("channel_post") or {}
    return str(msg["chat"]["id"])


def send(token: str, chat_id: str, text: str) -> None:
    resp = httpx.post(
        f"{API}/bot{token}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10
    )
    if resp.status_code != 200:
        raise SystemExit(f"Telegram error {resp.status_code}: {resp.text}")
    print("sent:", text[:64])
    time.sleep(1.6)


def build_paywall(issuer_pk: str) -> tuple[Paywall, InMemoryBudgetStore]:
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
    return paywall, budget


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN (your Hermes bot token).")
    chat_id = resolve_chat_id(token)

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
    paywall, budget = build_paywall(issuer_pk)

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

    short = bob_sub[:16] + "..."
    send(token, chat_id, "ShadowPay: agents paying agents on Algorand, bound to identity.")
    send(token, chat_id, f"bob@acme.example ({short}) is paying alice 0.005 USDC for her share.")

    settled = pay()
    assert isinstance(settled, Settled)
    send(
        token,
        chat_id,
        f"Paid. Settled on Algorand TestNet, txid {settled.outcome.transaction}. "
        "Bound to bob's verified identity, not an anonymous wallet.",
    )

    budget.revoke(bob_sub)
    revoked = pay()
    assert isinstance(revoked, Refused)
    send(
        token,
        chat_id,
        f"Now revoke bob's agent -> his next payment is REFUSED ({revoked.status}). "
        "His wallet is still fully funded. The identity is the kill switch.",
    )
    budget.restore(bob_sub)

    tampered = pay(tamper=True)
    assert isinstance(tampered, Refused)
    send(
        token,
        chat_id,
        f"A man-in-the-middle swaps the pay-to address -> REFUSED ({tampered.status}). "
        "The charge is bound to what was agreed.",
    )

    send(
        token,
        chat_id,
        "Know Your Agent for x402 — every payment tied to a verified, revocable agent.",
    )
    print("done -> chat", chat_id)


if __name__ == "__main__":
    main()
