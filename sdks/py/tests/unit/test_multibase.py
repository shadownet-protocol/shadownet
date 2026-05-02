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

# W3C did:key Test Vectors §1: Ed25519 public key 0xd75a98... encodes to
# z6MkiTBz1ymuepAQ4HEHYSF1H8quG5GLVVQR3djdX3mDooWp.
# Source: https://w3c-ccg.github.io/did-method-key/#example-1-key-method-w3c-ccg-2020
W3C_PUBLIC = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
W3C_DIDKEY_TAIL = "z6MkiTBz1ymuepAQ4HEHYSF1H8quG5GLVVQR3djdX3mDooWp"


def test_w3c_did_key_vector() -> None:
    encoded = encode_multibase_z(with_multicodec(ED25519_PUB_MULTICODEC, W3C_PUBLIC))
    assert encoded == W3C_DIDKEY_TAIL


def test_round_trip_arbitrary_bytes() -> None:
    payload = b"\x00\x01\x02\xff\xfe\xfd" * 8
    assert decode_multibase_z(encode_multibase_z(payload)) == payload


def test_decode_rejects_missing_prefix() -> None:
    with pytest.raises(MultibaseDecodeError):
        decode_multibase_z("6MkiTBz")


def test_decode_rejects_bad_alphabet() -> None:
    with pytest.raises(MultibaseDecodeError):
        decode_multibase_z("z0OIl")  # 0, O, I, l are not in base58btc


def test_strip_multicodec_round_trip() -> None:
    wrapped = with_multicodec(ED25519_PUB_MULTICODEC, W3C_PUBLIC)
    assert strip_multicodec(ED25519_PUB_MULTICODEC, wrapped) == W3C_PUBLIC


def test_strip_multicodec_wrong_prefix_rejected() -> None:
    with pytest.raises(MultibaseDecodeError):
        strip_multicodec(ED25519_PUB_MULTICODEC, b"\x00\x01abc")
