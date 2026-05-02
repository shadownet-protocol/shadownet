from __future__ import annotations

import time

import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.crypto.jwt import (
    JWTError,
    decode_header,
    decode_unverified_claims,
    sign_jwt,
    verify_jwt,
)


def test_round_trip() -> None:
    kp = Ed25519KeyPair.generate()
    now = int(time.time())
    token = sign_jwt(
        {"iss": "did:key:abc", "aud": "did:key:xyz", "iat": now, "exp": now + 60},
        kp,
    )
    claims = verify_jwt(token, kp, audience="did:key:xyz", issuer="did:key:abc")
    assert claims["iss"] == "did:key:abc"


def test_audience_mismatch_rejected() -> None:
    kp = Ed25519KeyPair.generate()
    now = int(time.time())
    token = sign_jwt({"aud": "did:key:xyz", "iat": now, "exp": now + 60}, kp)
    with pytest.raises(JWTError):
        verify_jwt(token, kp, audience="did:key:other")


def test_expired_rejected() -> None:
    kp = Ed25519KeyPair.generate()
    now = int(time.time())
    token = sign_jwt({"iat": now - 120, "exp": now - 60}, kp)
    with pytest.raises(JWTError):
        verify_jwt(token, kp)


def test_signature_failure_rejected() -> None:
    kp = Ed25519KeyPair.generate()
    other = Ed25519KeyPair.generate()
    now = int(time.time())
    token = sign_jwt({"iat": now, "exp": now + 60}, kp)
    with pytest.raises(JWTError):
        verify_jwt(token, other)


def test_required_claim_missing() -> None:
    kp = Ed25519KeyPair.generate()
    now = int(time.time())
    token = sign_jwt({"iat": now, "exp": now + 60}, kp)
    with pytest.raises(JWTError):
        verify_jwt(token, kp, required=["jti"])


def test_header_extras_pass_through() -> None:
    kp = Ed25519KeyPair.generate()
    now = int(time.time())
    token = sign_jwt(
        {"iat": now, "exp": now + 60},
        kp,
        header_extras={"kid": "did:key:abc#key-1", "typ": "vc+jwt"},
    )
    header = decode_header(token)
    assert header["typ"] == "vc+jwt"
    assert header["kid"] == "did:key:abc#key-1"
    assert header["alg"] == "EdDSA"


def test_decode_unverified_claims() -> None:
    kp = Ed25519KeyPair.generate()
    now = int(time.time())
    token = sign_jwt({"iss": "did:key:abc", "iat": now, "exp": now + 60}, kp)
    claims = decode_unverified_claims(token)
    assert claims["iss"] == "did:key:abc"
