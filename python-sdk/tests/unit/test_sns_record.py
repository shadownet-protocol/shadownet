from __future__ import annotations

import time

import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.document import DIDDocument, VerificationMethod
from shadownet.did.resolver import Resolver
from shadownet.sns.errors import ShadownameExpired, ShadownameInvalid
from shadownet.sns.record import (
    PublicKeyJWK,
    SNSRecord,
    parse_shadowname,
    sign_record,
    verify_record,
)


class _StubResolver(Resolver):
    def __init__(self, doc: DIDDocument) -> None:
        super().__init__(web=None)
        self._doc = doc

    async def resolve(self, did: str) -> DIDDocument:
        _ = did
        return self._doc


def test_parse_shadowname_canonical() -> None:
    assert parse_shadowname("Mahdi@Example.COM") == ("mahdi", "example.com")


@pytest.mark.parametrize(
    "bad",
    [
        "noatsymbol",
        "two@@signs",
        "@no.local",
        "no.provider@",
        "x@.invalid",
        ("a" * 64) + "@example.com",
    ],
)
def test_parse_shadowname_rejects(bad: str) -> None:
    with pytest.raises(ShadownameInvalid):
        parse_shadowname(bad)


def test_record_normalizes_shadowname() -> None:
    record = SNSRecord(
        shadowname="ALICE@EXAMPLE.com",
        did="did:key:z6MkAlice",
        endpoint="https://shadow.example/u/alice/a2a",
        publicKey=PublicKeyJWK(kty="OKP", crv="Ed25519", x="aaaa"),
        subjectType="person",
        ttl=300,
        issuedAt=int(time.time()),
        **{"shadownet:v": "0.1"},
    )
    assert record.shadowname == "alice@example.com"


def test_record_rejects_ttl_out_of_range() -> None:
    payload = {
        "shadowname": "alice@example.com",
        "did": "did:key:z6MkAlice",
        "endpoint": "https://shadow.example/u/alice/a2a",
        "publicKey": {"kty": "OKP", "crv": "Ed25519", "x": "aaaa"},
        "subjectType": "person",
        "ttl": 30,  # below 60s minimum
        "issuedAt": int(time.time()),
        "shadownet:v": "0.1",
    }
    with pytest.raises(Exception):  # noqa: B017 -- pydantic ValidationError
        SNSRecord.model_validate(payload)


def _provider_kit() -> tuple[Ed25519KeyPair, str, _StubResolver]:
    kp = Ed25519KeyPair.generate()
    provider_did = "did:web:x.example"
    doc = DIDDocument(
        id=provider_did,
        verificationMethod=[
            VerificationMethod(
                id=f"{provider_did}#key-1",
                type="JsonWebKey2020",
                controller=provider_did,
                publicKeyJwk=kp.public_jwk(),
            )
        ],
        authentication=[f"{provider_did}#key-1"],
        assertionMethod=[f"{provider_did}#key-1"],
    )
    return kp, provider_did, _StubResolver(doc)


def _record(issued_at: int, ttl: int = 60) -> SNSRecord:
    return SNSRecord(
        shadowname="alice@x.example",
        did="did:key:z6MkAlice",
        endpoint="https://shadow.example/u/alice/a2a",
        publicKey=PublicKeyJWK(kty="OKP", crv="Ed25519", x="aaaa"),
        subjectType="person",
        ttl=ttl,
        issuedAt=issued_at,
        **{"shadownet:v": "0.1"},
    )


async def test_verify_record_expired_raises_shadowname_expired() -> None:
    """Expiry path raises :class:`ShadownameExpired`, not bare :class:`ShadownameInvalid`."""
    kp, provider_did, resolver = _provider_kit()
    iat = 1_000_000
    record = _record(issued_at=iat, ttl=60)
    token = sign_record(provider_key=kp, provider_did=provider_did, record=record, issued_at=iat)

    with pytest.raises(ShadownameExpired):
        await verify_record(
            token,
            expected_provider_did=provider_did,
            resolver=resolver,
            now=iat + 61,
        )


async def test_verify_record_expired_still_caught_as_invalid() -> None:
    """``ShadownameExpired`` MUST remain a subclass of ``ShadownameInvalid`` (backward-compat)."""
    kp, provider_did, resolver = _provider_kit()
    iat = 1_000_000
    record = _record(issued_at=iat, ttl=60)
    token = sign_record(provider_key=kp, provider_did=provider_did, record=record, issued_at=iat)

    with pytest.raises(ShadownameInvalid):
        await verify_record(
            token,
            expected_provider_did=provider_did,
            resolver=resolver,
            now=iat + 61,
        )


async def test_verify_record_fresh_succeeds() -> None:
    kp, provider_did, resolver = _provider_kit()
    iat = 1_000_000
    record = _record(issued_at=iat, ttl=300)
    token = sign_record(provider_key=kp, provider_did=provider_did, record=record, issued_at=iat)

    verified = await verify_record(
        token,
        expected_provider_did=provider_did,
        resolver=resolver,
        now=iat + 30,
    )
    assert verified.shadowname == "alice@x.example"
