from __future__ import annotations

import base64
import json
from typing import Any

from shadownet_x402.asgi import to_response
from shadownet_x402.budget import InMemoryBudgetStore
from shadownet_x402.config import Settings
from shadownet_x402.facilitator import FakeFacilitator
from shadownet_x402.nonce import InMemoryNonceStore
from shadownet_x402.pop import mint_pop
from shadownet_x402.server import Challenge, Paywall, Refused, Settled
from shadownet_x402.settlement import PaidTerms, encode_x_payment

RESOURCE_URL = "https://venue.example/ticket"


def _paywall(issuer_pk: str) -> Paywall:
    return Paywall(
        Settings(
            pay_to="VENUEADDR",
            price_micro=5000,
            asset_id=10458941,
            network_caip2="algorand:testnet",
        ),
        nonce_store=InMemoryNonceStore(),
        budget_store=InMemoryBudgetStore(cap_micro=1_000_000),
        facilitator=FakeFacilitator(),
        resolve_issuer_key=lambda _iss: issuer_pk,
        check_revoked=lambda _credential: None,
    )


def _body(response: Any) -> dict[str, Any]:
    return json.loads(response.body)


def test_challenge_response(credential_jws: str, issuer_pk: str) -> None:
    challenge = _paywall(issuer_pk).process(resource_url=RESOURCE_URL, credential=credential_jws)
    assert isinstance(challenge, Challenge)
    response = to_response(challenge)
    assert response.status_code == 402
    assert response.headers["shadow-nonce"] == challenge.nonce
    assert _body(response)["accepts"][0]["maxAmountRequired"] == 5000


def test_refused_response() -> None:
    response = to_response(Refused(403, "nope"))
    assert response.status_code == 403
    assert _body(response)["error"] == "nope"


def test_settled_response(
    credential_jws: str, buyer_key: Any, buyer_sub: str, issuer_pk: str
) -> None:
    paywall = _paywall(issuer_pk)
    challenge = paywall.process(resource_url=RESOURCE_URL, credential=credential_jws)
    assert isinstance(challenge, Challenge)
    pop = mint_pop(buyer_key, sub=buyer_sub, audience=RESOURCE_URL, nonce=challenge.nonce)
    req = challenge.requirements
    x_payment = encode_x_payment(
        PaidTerms(
            network=req.network,
            asset=req.asset,
            amount=req.amount,
            pay_to=req.pay_to,
            payer=buyer_sub,
        )
    )
    settled = paywall.process(
        resource_url=RESOURCE_URL, credential=credential_jws, pop=pop, x_payment=x_payment
    )
    assert isinstance(settled, Settled)
    response = to_response(settled, body={"ticket": "ADMIT ONE"})
    assert response.status_code == 200
    assert _body(response)["ticket"] == "ADMIT ONE"
    raw = response.headers["x-payment-response"]
    receipt = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    assert receipt["transaction"] == "STUBTXID"
