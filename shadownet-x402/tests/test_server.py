from __future__ import annotations

from typing import Any

from shadownet_x402.budget import InMemoryBudgetStore
from shadownet_x402.config import Settings
from shadownet_x402.facilitator import FakeFacilitator
from shadownet_x402.nonce import InMemoryNonceStore
from shadownet_x402.pop import mint_pop
from shadownet_x402.server import Challenge, Paywall, Refused, Settled
from shadownet_x402.settlement import PaidTerms, encode_x_payment

RESOURCE_URL = "https://venue.example/ticket"


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "pay_to": "VENUEADDR",
        "price_micro": 5000,
        "budget_micro": 1_000_000,
        "network_caip2": "algorand:testnet",
        "asset_id": 10458941,
    }
    base.update(overrides)
    return Settings(**base)


def _paywall(issuer_pk: str, *, budget: InMemoryBudgetStore | None = None) -> Paywall:
    return Paywall(
        _settings(),
        nonce_store=InMemoryNonceStore(),
        budget_store=budget or InMemoryBudgetStore(cap_micro=1_000_000),
        facilitator=FakeFacilitator(),
        resolve_issuer_key=lambda _iss: issuer_pk,
        check_revoked=lambda _credential: None,
    )


def _pay(
    challenge: Challenge, buyer_key: Any, buyer_sub: str, *, pay_to: str | None = None
) -> tuple[str, str]:
    pop = mint_pop(buyer_key, sub=buyer_sub, audience=RESOURCE_URL, nonce=challenge.nonce)
    req = challenge.requirements
    terms = PaidTerms(
        network=req.network,
        asset=req.asset,
        amount=req.amount,
        pay_to=pay_to or req.pay_to,
        payer=buyer_sub,
    )
    return pop, encode_x_payment(terms)


def test_challenge_issues_nonce(credential_jws: str, issuer_pk: str) -> None:
    result = _paywall(issuer_pk).process(resource_url=RESOURCE_URL, credential=credential_jws)
    assert isinstance(result, Challenge)
    assert result.nonce
    assert result.requirements.amount == 5000


def test_full_settlement(
    credential_jws: str, buyer_key: Any, buyer_sub: str, issuer_pk: str
) -> None:
    paywall = _paywall(issuer_pk)
    challenge = paywall.process(resource_url=RESOURCE_URL, credential=credential_jws)
    assert isinstance(challenge, Challenge)
    pop, x_payment = _pay(challenge, buyer_key, buyer_sub)
    result = paywall.process(
        resource_url=RESOURCE_URL, credential=credential_jws, pop=pop, x_payment=x_payment
    )
    assert isinstance(result, Settled)
    assert result.outcome.transaction == "STUBTXID"
    assert result.identity.sub == buyer_sub


def test_replay_rejected(
    credential_jws: str, buyer_key: Any, buyer_sub: str, issuer_pk: str
) -> None:
    paywall = _paywall(issuer_pk)
    challenge = paywall.process(resource_url=RESOURCE_URL, credential=credential_jws)
    assert isinstance(challenge, Challenge)
    pop, x_payment = _pay(challenge, buyer_key, buyer_sub)
    first = paywall.process(
        resource_url=RESOURCE_URL, credential=credential_jws, pop=pop, x_payment=x_payment
    )
    assert isinstance(first, Settled)
    second = paywall.process(
        resource_url=RESOURCE_URL, credential=credential_jws, pop=pop, x_payment=x_payment
    )
    assert isinstance(second, Refused)
    assert second.status == 409


def test_tampered_payto_refused(
    credential_jws: str, buyer_key: Any, buyer_sub: str, issuer_pk: str
) -> None:
    paywall = _paywall(issuer_pk)
    challenge = paywall.process(resource_url=RESOURCE_URL, credential=credential_jws)
    assert isinstance(challenge, Challenge)
    pop, x_payment = _pay(challenge, buyer_key, buyer_sub, pay_to="ATTACKER")
    result = paywall.process(
        resource_url=RESOURCE_URL, credential=credential_jws, pop=pop, x_payment=x_payment
    )
    assert isinstance(result, Refused)
    assert result.status == 402


def test_revoked_identity_refused(credential_jws: str, buyer_sub: str, issuer_pk: str) -> None:
    budget = InMemoryBudgetStore(cap_micro=1_000_000)
    budget.revoke(buyer_sub)
    result = _paywall(issuer_pk, budget=budget).process(
        resource_url=RESOURCE_URL, credential=credential_jws
    )
    assert isinstance(result, Refused)
    assert result.status == 403


def test_over_budget_refused(
    credential_jws: str, buyer_key: Any, buyer_sub: str, issuer_pk: str
) -> None:
    budget = InMemoryBudgetStore(cap_micro=4000)
    paywall = _paywall(issuer_pk, budget=budget)
    challenge = paywall.process(resource_url=RESOURCE_URL, credential=credential_jws)
    assert isinstance(challenge, Challenge)
    pop, x_payment = _pay(challenge, buyer_key, buyer_sub)
    result = paywall.process(
        resource_url=RESOURCE_URL, credential=credential_jws, pop=pop, x_payment=x_payment
    )
    assert isinstance(result, Refused)
    assert result.status == 402
