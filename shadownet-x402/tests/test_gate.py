from __future__ import annotations

import pytest
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.status import StatusListError

from shadownet_x402.errors import GateError
from shadownet_x402.gate import run_identity_gate
from shadownet_x402.pop import mint_pop

RESOURCE_URL = "https://venue.example/ticket"
NONCE = "challenge-nonce"


def _gate(credential_jws, pop_jws, issuer_pk, *, check_revoked=lambda _c: None):
    return run_identity_gate(
        credential_jws=credential_jws,
        pop_jws=pop_jws,
        resource_url=RESOURCE_URL,
        nonce=NONCE,
        resolve_issuer_key=lambda _iss: issuer_pk,
        check_revoked=check_revoked,
    )


def test_accepts_valid(credential_jws: str, buyer_key, buyer_sub: str, issuer_pk: str) -> None:
    pop = mint_pop(buyer_key, sub=buyer_sub, audience=RESOURCE_URL, nonce=NONCE)
    identity = _gate(credential_jws, pop, issuer_pk)
    assert identity.sub == buyer_sub
    assert identity.org == "acme.example"


def test_rejects_revoked(credential_jws: str, buyer_key, buyer_sub: str, issuer_pk: str) -> None:
    pop = mint_pop(buyer_key, sub=buyer_sub, audience=RESOURCE_URL, nonce=NONCE)

    def revoked(_credential):
        raise StatusListError("revoked")

    with pytest.raises(GateError):
        _gate(credential_jws, pop, issuer_pk, check_revoked=revoked)


def test_rejects_wrong_nonce(
    credential_jws: str, buyer_key, buyer_sub: str, issuer_pk: str
) -> None:
    pop = mint_pop(buyer_key, sub=buyer_sub, audience=RESOURCE_URL, nonce="wrong")
    with pytest.raises(GateError):
        _gate(credential_jws, pop, issuer_pk)


def test_rejects_impostor_key(credential_jws: str, buyer_sub: str, issuer_pk: str) -> None:
    impostor = Ed25519KeyPair.generate()
    pop = mint_pop(impostor, sub=buyer_sub, audience=RESOURCE_URL, nonce=NONCE)
    with pytest.raises(GateError):
        _gate(credential_jws, pop, issuer_pk)
