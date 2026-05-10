"""Regressions for bugs found by shadownet-conformance against v0.1.1.

Each test pins down a behavior we got wrong in v0.1.1:

1. `build_subject_auth` was emitting headers without `kid` (RFC-0004
   §Common: subject authentication shows kid in the example header).
2. `mint_session_token` had the same header issue (symmetric fix).
3. `build_csr` had the same header issue — JWT body signed by the holder
   but no kid in the header.
4. `LevelPolicy.method` rejected non-URN URIs (RFC-0004 §Policy document
   says method is "operator-defined URI" — any scheme).
5. `ProofSession.method` had the same over-strict regex.
"""

from __future__ import annotations

import time

from shadownet.a2a.session import mint_session_token
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.crypto.jwt import decode_header
from shadownet.did.key import derive_did_key
from shadownet.sca.client import ProofSession
from shadownet.sca.csr import build_csr, build_subject_auth
from shadownet.sca.policy import SCAPolicy


def _holder() -> tuple[Ed25519KeyPair, str]:
    kp = Ed25519KeyPair.generate()
    return kp, derive_did_key(kp.public_bytes)


# --- Bug 1: subject-auth header MUST carry kid ---


def test_subject_auth_header_includes_kid_default() -> None:
    kp, did = _holder()
    token = build_subject_auth(holder_key=kp, holder_did=did, sca_did="did:web:sca.sh4dow.org")
    header = decode_header(token)
    assert header["kid"] == did
    assert header["alg"] == "EdDSA"
    assert header["typ"] == "JWT"


def test_subject_auth_header_kid_override() -> None:
    kp, did = _holder()
    explicit_kid = "did:web:org.example#key-2"
    token = build_subject_auth(
        holder_key=kp,
        holder_did=did,
        sca_did="did:web:sca.sh4dow.org",
        kid=explicit_kid,
    )
    assert decode_header(token)["kid"] == explicit_kid


# --- Bug 1b: CSR header carries kid (parallel to subject-auth) ---


def test_csr_header_includes_kid_default() -> None:
    kp, did = _holder()
    token = build_csr(
        holder_key=kp,
        holder_did=did,
        sca_did="did:web:sca.sh4dow.org",
        level="urn:shadownet:level:L1",
        subject_type="person",
    )
    header = decode_header(token)
    assert header["kid"] == did
    assert header["alg"] == "EdDSA"


def test_csr_header_kid_override() -> None:
    kp, did = _holder()
    explicit_kid = "did:web:org.example#signing-key"
    token = build_csr(
        holder_key=kp,
        holder_did=did,
        sca_did="did:web:sca.sh4dow.org",
        level="urn:shadownet:level:L2",
        subject_type="organization",
        kid=explicit_kid,
    )
    assert decode_header(token)["kid"] == explicit_kid


# --- Bug 2: session-token header carries kid for symmetry ---


def test_session_token_header_includes_kid_default() -> None:
    kp, did = _holder()
    callee_did = derive_did_key(Ed25519KeyPair.generate().public_bytes)
    token = mint_session_token(holder_key=kp, holder_did=did, audience_did=callee_did)
    header = decode_header(token)
    assert header["kid"] == did


def test_session_token_header_kid_override() -> None:
    kp, did = _holder()
    callee_did = derive_did_key(Ed25519KeyPair.generate().public_bytes)
    explicit_kid = "did:web:caller.example#k1"
    token = mint_session_token(
        holder_key=kp,
        holder_did=did,
        audience_did=callee_did,
        kid=explicit_kid,
    )
    assert decode_header(token)["kid"] == explicit_kid


# --- Bug 3 & 4: method URIs are operator-defined, not URN-only ---


def test_policy_accepts_https_method_uri() -> None:
    """RFC-0004 §Policy document: method is an operator-defined URI."""
    payload = {
        "issuer": "did:web:sca.sh4dow.org",
        "shadownet:v": "0.1",
        "levels": [
            {
                "level": "urn:shadownet:level:L1",
                "method": "https://sca.sh4dow.org/methods/email-verification",
            }
        ],
        "freshnessWindowSeconds": 86400,
        "statusListBase": "https://sca.sh4dow.org/status/",
    }
    policy = SCAPolicy.model_validate(payload)
    assert policy.method_for("urn:shadownet:level:L1") == (
        "https://sca.sh4dow.org/methods/email-verification"
    )


def test_policy_accepts_method_uri_with_arbitrary_scheme() -> None:
    """No scheme restriction — any URI shape (vendor: scheme, did: URI, etc.)."""
    payload = {
        "issuer": "did:web:sca.sh4dow.org",
        "shadownet:v": "0.1",
        "levels": [
            {
                "level": "urn:shadownet:level:L2",
                "method": "did:web:idverify.example",
            }
        ],
        "freshnessWindowSeconds": 86400,
        "statusListBase": "https://sca.sh4dow.org/status/",
    }
    policy = SCAPolicy.model_validate(payload)
    assert policy.method_for("urn:shadownet:level:L2") == "did:web:idverify.example"


def test_proof_session_accepts_https_method_uri() -> None:
    payload = {
        "shadownet:v": "0.1",
        "sessionId": "ses-01H9X",
        "expiresAt": int(time.time()) + 3600,
        "method": "https://verify.stripe.com/methods/document-check-v1",
        "next": {"kind": "redirect", "url": "https://verify.stripe.com/abc123", "ttl": 600},
    }
    session = ProofSession.model_validate(payload)
    assert session.method == "https://verify.stripe.com/methods/document-check-v1"
