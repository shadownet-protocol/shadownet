from __future__ import annotations

import pytest

from shadownet.crypto.multibase import (
    ED25519_PUB_MULTICODEC,
    MultibaseDecodeError,
    decode_multibase_z,
    encode_multibase_z,
    strip_multicodec,
    with_multicodec,
)


def test_base58_known_vector_hello_world() -> None:
    # Standard Bitcoin/base58btc fixture: "Hello World!" -> "2NEpo7TZRRrLZSi2U".
    assert encode_multibase_z(b"Hello World!") == "z2NEpo7TZRRrLZSi2U"
    assert decode_multibase_z("z2NEpo7TZRRrLZSi2U") == b"Hello World!"


def test_base58_preserves_leading_zero_bytes() -> None:
    assert encode_multibase_z(b"\x00\x00ab") == "z11" + encode_multibase_z(b"ab")[1:]
    assert decode_multibase_z("z111") == b"\x00\x00\x00"


def test_round_trip_random_bytes() -> None:
    payload = b"\x00\x01\x02\xff\xfe\xfd" * 8
    assert decode_multibase_z(encode_multibase_z(payload)) == payload


def test_ed25519_zero_pubkey_did_key_tail() -> None:
    # Canonical did:key for the all-zero Ed25519 public key, as produced by every
    # spec-conformant did:key library (verifies the 0xed01 multicodec varint + base58btc together).
    encoded = encode_multibase_z(with_multicodec(ED25519_PUB_MULTICODEC, b"\x00" * 32))
    assert encoded == "z6MkeTG3bFFSLYVU7VqhgZxqr6YzpaGrQtFMh1uvqGy1vDnP"


def test_decode_rejects_missing_prefix() -> None:
    with pytest.raises(MultibaseDecodeError):
        decode_multibase_z("6MkiTBz")


def test_decode_rejects_bad_alphabet() -> None:
    with pytest.raises(MultibaseDecodeError):
        decode_multibase_z("z0OIl")  # 0, O, I, l are not in base58btc


def test_strip_multicodec_round_trip() -> None:
    payload = b"\xaa" * 32
    wrapped = with_multicodec(ED25519_PUB_MULTICODEC, payload)
    assert strip_multicodec(ED25519_PUB_MULTICODEC, wrapped) == payload


def test_strip_multicodec_wrong_prefix_rejected() -> None:
    with pytest.raises(MultibaseDecodeError):
        strip_multicodec(ED25519_PUB_MULTICODEC, b"\x00\x01abc")
