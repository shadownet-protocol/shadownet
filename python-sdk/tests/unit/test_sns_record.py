from __future__ import annotations

import time

import pytest

from shadownet.sns.errors import ShadownameInvalid
from shadownet.sns.record import PublicKeyJWK, SNSRecord, parse_shadowname


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
