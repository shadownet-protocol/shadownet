"""Buyer-side x402 client that attaches a verifiable Shadow identity to each paid request."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shadownet_x402.pop import mint_pop
from shadownet_x402.requirements import PaymentRequirements
from shadownet_x402.settlement import PaidTerms, encode_x_payment

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx
    from shadownet.crypto.ed25519 import Ed25519KeyPair

    PaymentBuilder = Callable[[PaymentRequirements], str]


class ShadowX402Client:
    """Runs the x402 handshake: present identity, answer the 402, retry with payment."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        shadow_key: Ed25519KeyPair,
        shadow_sub: str,
        credential_jws: str,
        payer: str,
        build_payment: PaymentBuilder | None = None,
    ) -> None:
        self._http = http
        self._shadow_key = shadow_key
        self._shadow_sub = shadow_sub
        self._credential = credential_jws
        self._payer = payer
        self._build_payment = build_payment or self._stub_payment

    async def get(self, url: str) -> httpx.Response:
        challenge = await self._http.get(url, headers={"Shadow-Credential": self._credential})
        if challenge.status_code != 402:
            return challenge
        nonce = challenge.headers.get("Shadow-Nonce", "")
        requirements = PaymentRequirements.model_validate(challenge.json()["accepts"][0])
        pop = mint_pop(self._shadow_key, sub=self._shadow_sub, audience=url, nonce=nonce)
        x_payment = self._build_payment(requirements)
        return await self._http.get(
            url,
            headers={
                "Shadow-Credential": self._credential,
                "Shadow-PoP": pop,
                "X-PAYMENT": x_payment,
            },
        )

    def _stub_payment(self, requirements: PaymentRequirements) -> str:
        terms = PaidTerms(
            network=requirements.network,
            asset=requirements.asset,
            amount=requirements.amount,
            pay_to=requirements.pay_to,
            payer=self._payer,
        )
        return encode_x_payment(terms)
