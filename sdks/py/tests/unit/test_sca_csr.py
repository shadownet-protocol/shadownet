from __future__ import annotations

import time

import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.key import derive_did_key
from shadownet.did.resolver import Resolver
from shadownet.sca.csr import (
    DEFAULT_AUTH_TTL_SECONDS,
    build_csr,
    build_subject_auth,
    verify_csr,
    verify_subject_auth,
)
from shadownet.sca.errors import CSRInvalid


@pytest.fixture
def holder() -> tuple[Ed25519KeyPair, str]:
    kp = Ed25519KeyPair.generate()
    return kp, derive_did_key(kp.public_bytes)


SCA_DID = "did:web:sca.example"


async def test_csr_round_trip(holder) -> None:
    kp, did = holder
    token = build_csr(
        holder_key=kp,
        holder_did=did,
        sca_did=SCA_DID,
        level="urn:shadownet:level:L1",
        subject_type="person",
    )
    csr = await verify_csr(token, resolver=Resolver(), expected_audience=SCA_DID)
    assert csr.iss == did
    assert csr.request.level == "urn:shadownet:level:L1"


async def test_csr_audience_mismatch(holder) -> None:
    kp, did = holder
    token = build_csr(
        holder_key=kp,
        holder_did=did,
        sca_did="did:web:other.example",
        level="urn:shadownet:level:L1",
        subject_type="person",
    )
    with pytest.raises(CSRInvalid):
        await verify_csr(token, resolver=Resolver(), expected_audience=SCA_DID)


async def test_subject_auth_round_trip(holder) -> None:
    kp, did = holder
    token = build_subject_auth(holder_key=kp, holder_did=did, sca_did=SCA_DID)
    auth = await verify_subject_auth(token, resolver=Resolver(), expected_audience=SCA_DID)
    assert auth.iss == did
    assert auth.aud == SCA_DID
    assert auth.purpose == "sca-request"


async def test_subject_auth_ttl_cap_enforced(holder) -> None:
    kp, did = holder
    with pytest.raises(ValueError):
        build_subject_auth(
            holder_key=kp,
            holder_did=did,
            sca_did=SCA_DID,
            ttl_seconds=DEFAULT_AUTH_TTL_SECONDS + 1,
        )


async def test_subject_auth_expired(holder) -> None:
    kp, did = holder
    token = build_subject_auth(
        holder_key=kp,
        holder_did=did,
        sca_did=SCA_DID,
        issued_at=int(time.time()) - 120,
        ttl_seconds=DEFAULT_AUTH_TTL_SECONDS,
    )
    with pytest.raises(CSRInvalid):
        await verify_subject_auth(token, resolver=Resolver(), expected_audience=SCA_DID)
