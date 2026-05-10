from __future__ import annotations

import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair, SignatureError


def test_generate_yields_valid_pair() -> None:
    kp = Ed25519KeyPair.generate()
    assert kp.has_private
    sig = kp.sign(b"hello")
    kp.verify(sig, b"hello")


def test_from_seed_is_deterministic() -> None:
    seed = bytes(range(32))
    a = Ed25519KeyPair.from_seed(seed)
    b = Ed25519KeyPair.from_seed(seed)
    assert a.public_bytes == b.public_bytes
    assert a.sign(b"x") == b.sign(b"x")


def test_seed_wrong_length_rejected() -> None:
    with pytest.raises(SignatureError):
        Ed25519KeyPair.from_seed(b"short")


def test_jwk_round_trip_public_only() -> None:
    kp = Ed25519KeyPair.generate()
    jwk = kp.public_jwk()
    restored = Ed25519KeyPair.from_jwk(jwk)
    assert restored.public_bytes == kp.public_bytes
    assert not restored.has_private
    with pytest.raises(SignatureError):
        restored.sign(b"x")


def test_jwk_round_trip_with_private() -> None:
    kp = Ed25519KeyPair.generate()
    jwk = kp.private_jwk()
    restored = Ed25519KeyPair.from_jwk(jwk)
    assert restored.has_private
    sig = restored.sign(b"hello")
    kp.verify(sig, b"hello")


def test_jwk_rejects_non_ed25519() -> None:
    with pytest.raises(SignatureError):
        Ed25519KeyPair.from_jwk({"kty": "EC", "crv": "P-256", "x": "abc", "y": "def"})


def test_verify_failure_raises() -> None:
    kp = Ed25519KeyPair.generate()
    other = Ed25519KeyPair.generate()
    sig = kp.sign(b"hello")
    with pytest.raises(SignatureError):
        other.verify(sig, b"hello")
