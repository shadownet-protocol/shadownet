from __future__ import annotations

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.crypto.multibase import (
    ED25519_PUB_MULTICODEC,
    decode_multibase_z,
    encode_multibase_z,
    strip_multicodec,
    with_multicodec,
)
from shadownet.did.document import DIDDocument, VerificationMethod
from shadownet.did.errors import DIDSyntaxError

# RFC-0002 §did:key — individuals; multibase(0xed01 || ed25519-public-key).

__all__ = ["derive_did_key", "did_key_document", "parse_did_key"]

_DID_KEY_PREFIX = "did:key:"


def derive_did_key(public_bytes: bytes) -> str:
    """Encode an Ed25519 32-byte public key as a ``did:key`` DID."""
    if len(public_bytes) != 32:
        raise DIDSyntaxError("Ed25519 public key must be exactly 32 bytes")
    tail = encode_multibase_z(with_multicodec(ED25519_PUB_MULTICODEC, public_bytes))
    return _DID_KEY_PREFIX + tail


def parse_did_key(did: str) -> Ed25519KeyPair:
    """Parse a ``did:key`` DID and return the public-only Ed25519 keypair."""
    if not did.startswith(_DID_KEY_PREFIX):
        raise DIDSyntaxError(f"not a did:key DID: {did!r}")
    tail = did.removeprefix(_DID_KEY_PREFIX).split("#", 1)[0]
    if not tail.startswith("z"):
        raise DIDSyntaxError("did:key tail must be a base58btc multibase ('z')")
    raw = decode_multibase_z(tail)
    public = strip_multicodec(ED25519_PUB_MULTICODEC, raw)
    if len(public) != 32:
        raise DIDSyntaxError("did:key payload is not a 32-byte Ed25519 key")
    return Ed25519KeyPair.from_public_bytes(public)


def did_key_document(did: str) -> DIDDocument:
    """Synthesize the canonical DID document for a ``did:key`` DID, locally."""
    kp = parse_did_key(did)
    base = did.split("#", 1)[0]
    multibase_pub = encode_multibase_z(with_multicodec(ED25519_PUB_MULTICODEC, kp.public_bytes))
    fragment_id = base + "#" + multibase_pub
    vm = VerificationMethod(
        id=fragment_id,
        type="Ed25519VerificationKey2020",
        controller=base,
        public_key_multibase=multibase_pub,
    )
    return DIDDocument(
        id=base,
        verification_method=[vm],
        authentication=[fragment_id],
        assertion_method=[fragment_id],
    )
