from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from fastapi import FastAPI, Request

from shadownet_x402.asgi import read_payment_headers, to_response
from shadownet_x402.budget import InMemoryBudgetStore
from shadownet_x402.client import ShadowX402Client
from shadownet_x402.config import Settings
from shadownet_x402.facilitator import FakeFacilitator
from shadownet_x402.nonce import InMemoryNonceStore
from shadownet_x402.server import Paywall, Settled
from shadownet_x402.settlement import PaidTerms, encode_x_payment

if TYPE_CHECKING:
    from shadownet_x402.requirements import PaymentRequirements

BASE_URL = "http://venue.example"
TICKET_URL = f"{BASE_URL}/ticket"


def _app(issuer_pk: str) -> FastAPI:
    paywall = Paywall(
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
    app = FastAPI()

    @app.get("/ticket")
    def ticket(request: Request) -> Any:
        result = paywall.process(resource_url=str(request.url), **read_payment_headers(request))
        if isinstance(result, Settled):
            return to_response(
                result, body={"ticket": "ADMIT ONE", "txid": result.outcome.transaction}
            )
        return to_response(result)

    return app


async def test_buyer_pays_and_gets_ticket(
    credential_jws: str, buyer_key: Any, buyer_sub: str, issuer_pk: str
) -> None:
    transport = httpx.ASGITransport(app=_app(issuer_pk))
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http:
        client = ShadowX402Client(
            http,
            shadow_key=buyer_key,
            shadow_sub=buyer_sub,
            credential_jws=credential_jws,
            payer="BUYER",
        )
        response = await client.get(TICKET_URL)
    assert response.status_code == 200
    assert response.json()["ticket"] == "ADMIT ONE"


async def test_tampered_payto_refused(
    credential_jws: str, buyer_key: Any, buyer_sub: str, issuer_pk: str
) -> None:
    def tamper(requirements: PaymentRequirements) -> str:
        terms = PaidTerms(
            network=requirements.network,
            asset=requirements.asset,
            amount=requirements.amount,
            pay_to="ATTACKER",
            payer="BUYER",
        )
        return encode_x_payment(terms)

    transport = httpx.ASGITransport(app=_app(issuer_pk))
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http:
        client = ShadowX402Client(
            http,
            shadow_key=buyer_key,
            shadow_sub=buyer_sub,
            credential_jws=credential_jws,
            payer="BUYER",
            build_payment=tamper,
        )
        response = await client.get(TICKET_URL)
    assert response.status_code == 402
