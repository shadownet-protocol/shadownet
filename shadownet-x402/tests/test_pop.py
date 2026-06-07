from __future__ import annotations

import time

import pytest
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.identifiers import encode_public_key

from shadownet_x402.errors import PoPError
from shadownet_x402.pop import mint_pop, verify_pop

AUD = "https://venue.example/ticket"
NONCE = "n-123"


def _resolver(key: Ed25519KeyPair):
    public = Ed25519KeyPair.from_public_bytes(key.public_bytes)
    return lambda _sub: public


def test_roundtrip() -> None:
    key = Ed25519KeyPair.generate()
    sub = encode_public_key(key.public_bytes)
    token = mint_pop(key, sub=sub, audience=AUD, nonce=NONCE)
    verify_pop(
        token,
        expected_sub=sub,
        expected_audience=AUD,
        expected_nonce=NONCE,
        resolve_subject_key=_resolver(key),
    )


def test_wrong_nonce() -> None:
    key = Ed25519KeyPair.generate()
    sub = encode_public_key(key.public_bytes)
    token = mint_pop(key, sub=sub, audience=AUD, nonce=NONCE)
    with pytest.raises(PoPError):
        verify_pop(
            token,
            expected_sub=sub,
            expected_audience=AUD,
            expected_nonce="other",
            resolve_subject_key=_resolver(key),
        )


def test_wrong_audience() -> None:
    key = Ed25519KeyPair.generate()
    sub = encode_public_key(key.public_bytes)
    token = mint_pop(key, sub=sub, audience=AUD, nonce=NONCE)
    with pytest.raises(PoPError):
        verify_pop(
            token,
            expected_sub=sub,
            expected_audience="https://evil.example/ticket",
            expected_nonce=NONCE,
            resolve_subject_key=_resolver(key),
        )


def test_wrong_key() -> None:
    key = Ed25519KeyPair.generate()
    impostor = Ed25519KeyPair.generate()
    sub = encode_public_key(key.public_bytes)
    token = mint_pop(key, sub=sub, audience=AUD, nonce=NONCE)
    with pytest.raises(PoPError):
        verify_pop(
            token,
            expected_sub=sub,
            expected_audience=AUD,
            expected_nonce=NONCE,
            resolve_subject_key=_resolver(impostor),
        )


def test_expired() -> None:
    key = Ed25519KeyPair.generate()
    sub = encode_public_key(key.public_bytes)
    past = int(time.time()) - 1000
    token = mint_pop(key, sub=sub, audience=AUD, nonce=NONCE, now=past, lifetime=60)
    with pytest.raises(PoPError):
        verify_pop(
            token,
            expected_sub=sub,
            expected_audience=AUD,
            expected_nonce=NONCE,
            resolve_subject_key=_resolver(key),
            leeway=0,
        )
