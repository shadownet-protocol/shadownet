from __future__ import annotations

import time

import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.key import derive_did_key
from shadownet.did.resolver import Resolver
from shadownet.vc.credential import issue_credential, new_credential
from shadownet.vc.errors import FreshnessExpired
from shadownet.vc.freshness import (
    DEFAULT_FRESHNESS_WINDOW_SECONDS,
    freshness_required,
    mint_freshness_proof,
    verify_freshness,
)


@pytest.fixture
def issuer() -> tuple[Ed25519KeyPair, str]:
    kp = Ed25519KeyPair.generate()
    return kp, derive_did_key(kp.public_bytes)


def _build_credential(issuer_kp: Ed25519KeyPair, issuer_did: str, *, age_seconds: int = 0):
    subject = derive_did_key(Ed25519KeyPair.generate().public_bytes)
    iat = int(time.time()) - age_seconds
    cred = new_credential(
        issuer=issuer_did,
        subject=subject,
        level="urn:shadownet:level:L2",
        subject_type="person",
        issued_at=iat,
    )
    token = issue_credential(issuer_key=issuer_kp, issuer_kid=issuer_did, credential=cred)
    return cred, token


async def test_freshness_proof_round_trip(issuer) -> None:
    issuer_kp, issuer_did = issuer
    cred, _ = _build_credential(
        issuer_kp, issuer_did, age_seconds=DEFAULT_FRESHNESS_WINDOW_SECONDS + 60
    )
    proof_jwt = mint_freshness_proof(
        issuer_key=issuer_kp,
        issuer_did=issuer_did,
        issuer_kid=issuer_did,
        credential_jti=cred.jti,
    )
    verified = await verify_freshness(proof_jwt, cred, resolver=Resolver(), now=int(time.time()))
    assert verified.sub == cred.jti
    assert verified.iss == cred.iss


async def test_freshness_proof_outside_window(issuer) -> None:
    issuer_kp, issuer_did = issuer
    cred, _ = _build_credential(
        issuer_kp, issuer_did, age_seconds=DEFAULT_FRESHNESS_WINDOW_SECONDS + 60
    )
    old_proof_iat = int(time.time()) - DEFAULT_FRESHNESS_WINDOW_SECONDS - 600
    proof_jwt = mint_freshness_proof(
        issuer_key=issuer_kp,
        issuer_did=issuer_did,
        issuer_kid=issuer_did,
        credential_jti=cred.jti,
        issued_at=old_proof_iat,
        lifetime_seconds=86400 * 2,  # not yet exp
    )
    with pytest.raises(FreshnessExpired):
        await verify_freshness(proof_jwt, cred, resolver=Resolver(), now=int(time.time()))


async def test_freshness_proof_subject_mismatch(issuer) -> None:
    issuer_kp, issuer_did = issuer
    cred, _ = _build_credential(issuer_kp, issuer_did)
    proof_jwt = mint_freshness_proof(
        issuer_key=issuer_kp,
        issuer_did=issuer_did,
        issuer_kid=issuer_did,
        credential_jti="urn:uuid:wrong",
    )
    with pytest.raises(FreshnessExpired):
        await verify_freshness(proof_jwt, cred, resolver=Resolver(), now=int(time.time()))


def test_freshness_required_threshold(issuer) -> None:
    issuer_kp, issuer_did = issuer
    fresh_cred, _ = _build_credential(issuer_kp, issuer_did, age_seconds=10)
    stale_cred, _ = _build_credential(
        issuer_kp, issuer_did, age_seconds=DEFAULT_FRESHNESS_WINDOW_SECONDS + 60
    )
    now = int(time.time())
    assert not freshness_required(fresh_cred, now=now)
    assert freshness_required(stale_cred, now=now)
