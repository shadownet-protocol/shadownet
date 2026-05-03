# RFC-0004 §Common: subject authentication

"""SCA MUST verify the subject-auth JWT on every authenticated endpoint."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest
from shadownet.crypto.jwt import sign_jwt
from shadownet.sca.csr import build_subject_auth

if TYPE_CHECKING:
    from shadownet.crypto.ed25519 import Ed25519KeyPair

pytestmark = pytest.mark.class_("sca")


@pytest.fixture
async def sca_did(sca_url, http) -> str:
    return (await http.get(f"{sca_url}/.well-known/did.json")).json()["id"]


@pytest.mark.network
@pytest.mark.rfc("0004", section="SubjectAuth", requirement="missing_auth_rejected")
async def test_proof_start_without_auth_rejected(sca_url, http, subject_did):
    """An authenticated endpoint without `Authorization` header MUST fail."""
    resp = await http.post(
        f"{sca_url}/proof/start",
        json={
            "shadownet:v": "0.1",
            "subject": subject_did,
            "level": "urn:shadownet:level:L1",
        },
    )
    assert resp.status_code in {401, 403}, (
        f"missing auth should be rejected with 401/403, got {resp.status_code}"
    )


@pytest.mark.network
@pytest.mark.rfc("0004", section="SubjectAuth", requirement="wrong_audience_rejected")
async def test_proof_start_with_wrong_aud_rejected(
    sca_url,
    http,
    subject_keypair: Ed25519KeyPair,
    subject_did: str,
):
    bad_auth = build_subject_auth(
        holder_key=subject_keypair,
        holder_did=subject_did,
        sca_did="did:web:not-this-sca.example",
    )
    resp = await http.post(
        f"{sca_url}/proof/start",
        headers={"Authorization": f"Bearer {bad_auth}"},
        json={
            "shadownet:v": "0.1",
            "subject": subject_did,
            "level": "urn:shadownet:level:L1",
        },
    )
    assert resp.status_code in {401, 403}


@pytest.mark.network
@pytest.mark.rfc("0004", section="SubjectAuth", requirement="expired_auth_rejected")
async def test_proof_start_with_expired_auth_rejected(
    sca_url,
    http,
    subject_keypair: Ed25519KeyPair,
    subject_did: str,
    sca_did: str,
):
    iat = int(time.time()) - 3600
    claims = {
        "iss": subject_did,
        "aud": sca_did,
        "iat": iat,
        "exp": iat + 60,  # expired an hour ago
        "jti": "00000000-0000-4000-8000-000000000099",
        "shadownet:v": "0.1",
        "purpose": "sca-request",
    }
    expired = sign_jwt(
        claims,
        subject_keypair,
        header_extras={"typ": "JWT", "kid": f"{subject_did}#key-1"},
    )
    resp = await http.post(
        f"{sca_url}/proof/start",
        headers={"Authorization": f"Bearer {expired}"},
        json={
            "shadownet:v": "0.1",
            "subject": subject_did,
            "level": "urn:shadownet:level:L1",
        },
    )
    assert resp.status_code in {401, 403}


@pytest.mark.network
@pytest.mark.rfc("0004", section="SubjectAuth", requirement="ttl_at_most_60s")
async def test_subject_auth_ttl_capped_at_60s(
    sca_url,
    http,
    subject_keypair: Ed25519KeyPair,
    subject_did: str,
    sca_did: str,
):
    """RFC-0004: subject-auth JWT exp - iat MUST be ≤ 60s. The SCA MAY reject anything longer."""
    iat = int(time.time())
    claims = {
        "iss": subject_did,
        "aud": sca_did,
        "iat": iat,
        "exp": iat + 600,  # 10min — over the cap
        "jti": "00000000-0000-4000-8000-000000000098",
        "shadownet:v": "0.1",
        "purpose": "sca-request",
    }
    long_lived = sign_jwt(
        claims,
        subject_keypair,
        header_extras={"typ": "JWT", "kid": f"{subject_did}#key-1"},
    )
    resp = await http.post(
        f"{sca_url}/proof/start",
        headers={"Authorization": f"Bearer {long_lived}"},
        json={
            "shadownet:v": "0.1",
            "subject": subject_did,
            "level": "urn:shadownet:level:L1",
        },
    )
    # The SCA MAY enforce or accept; the spec says "MUST be ≤ 60s" on the
    # caller side. We assert it does not crash and either accepts (200) or
    # rejects (4xx) — never a 5xx.
    assert resp.status_code < 500, f"long-lived auth caused server error: {resp.status_code}"
