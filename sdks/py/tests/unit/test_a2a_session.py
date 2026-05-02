from __future__ import annotations

import time

import pytest

from shadownet.a2a.errors import PresentationInvalidError
from shadownet.a2a.session import (
    DEFAULT_SESSION_TOKEN_TTL,
    mint_session_token,
    verify_session_token,
)
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.key import derive_did_key
from shadownet.did.resolver import Resolver


@pytest.fixture
def caller() -> tuple[Ed25519KeyPair, str]:
    kp = Ed25519KeyPair.generate()
    return kp, derive_did_key(kp.public_bytes)


@pytest.fixture
def callee_did() -> str:
    return derive_did_key(Ed25519KeyPair.generate().public_bytes)


async def test_session_token_round_trip(caller, callee_did) -> None:
    kp, did = caller
    token = mint_session_token(holder_key=kp, holder_did=did, audience_did=callee_did)
    parsed = await verify_session_token(token, expected_audience=callee_did, resolver=Resolver())
    assert parsed.iss == did
    assert parsed.aud == callee_did
    assert parsed.purpose == "a2a-session"


async def test_session_token_audience_mismatch(caller, callee_did) -> None:
    kp, did = caller
    token = mint_session_token(holder_key=kp, holder_did=did, audience_did=callee_did)
    with pytest.raises(PresentationInvalidError):
        await verify_session_token(
            token,
            expected_audience="did:key:z6MkOther",
            resolver=Resolver(),
        )


async def test_session_token_ttl_capped(caller, callee_did) -> None:
    kp, did = caller
    with pytest.raises(ValueError):
        mint_session_token(
            holder_key=kp,
            holder_did=did,
            audience_did=callee_did,
            ttl_seconds=DEFAULT_SESSION_TOKEN_TTL + 1,
        )


async def test_session_token_expired(caller, callee_did) -> None:
    kp, did = caller
    token = mint_session_token(
        holder_key=kp,
        holder_did=did,
        audience_did=callee_did,
        issued_at=int(time.time()) - 600,
        ttl_seconds=DEFAULT_SESSION_TOKEN_TTL,
    )
    with pytest.raises(PresentationInvalidError):
        await verify_session_token(token, expected_audience=callee_did, resolver=Resolver())
