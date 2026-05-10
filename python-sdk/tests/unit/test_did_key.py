from __future__ import annotations

import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.errors import DIDSyntaxError
from shadownet.did.key import derive_did_key, did_key_document, parse_did_key


def test_derive_did_key_round_trip() -> None:
    kp = Ed25519KeyPair.generate()
    did = derive_did_key(kp.public_bytes)
    assert did.startswith("did:key:z6Mk")
    parsed = parse_did_key(did)
    assert parsed.public_bytes == kp.public_bytes


def test_zero_public_key_canonical_did_key() -> None:
    did = derive_did_key(b"\x00" * 32)
    assert did == "did:key:z6MkeTG3bFFSLYVU7VqhgZxqr6YzpaGrQtFMh1uvqGy1vDnP"


def test_did_key_document_has_one_method() -> None:
    kp = Ed25519KeyPair.generate()
    did = derive_did_key(kp.public_bytes)
    doc = did_key_document(did)
    assert doc.id == did
    assert len(doc.verification_method) == 1
    assert doc.authentication[0] == doc.verification_method[0].id
    derived = doc.find_key()
    assert derived.public_bytes == kp.public_bytes


def test_did_key_document_strips_fragment() -> None:
    kp = Ed25519KeyPair.generate()
    base = derive_did_key(kp.public_bytes)
    doc = did_key_document(base + "#whatever")
    assert doc.id == base


@pytest.mark.parametrize(
    "bad",
    [
        "did:web:example.com",
        "did:key:notmultibase",
        "key:z6MkeTG3bFFSLYVU7VqhgZxqr6YzpaGrQtFMh1uvqGy1vDnP",
    ],
)
def test_parse_did_key_rejects_garbage(bad: str) -> None:
    with pytest.raises(DIDSyntaxError):
        parse_did_key(bad)


def test_derive_did_key_wrong_length() -> None:
    with pytest.raises(DIDSyntaxError):
        derive_did_key(b"too short")
