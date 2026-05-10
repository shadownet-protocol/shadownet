from __future__ import annotations

import time

import httpx
import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.document import DIDDocument, VerificationMethod
from shadownet.did.resolver import Resolver
from shadownet.did.web import WebDIDResolver
from shadownet.sns.client import SNSClient
from shadownet.sns.errors import ShadownameNotFound, ShadownameTombstoned
from shadownet.sns.record import PublicKeyJWK, SNSRecord, sign_record

PROVIDER_HOST = "x.example"
PROVIDER_DID = f"did:web:{PROVIDER_HOST}"


@pytest.fixture
def provider_keypair() -> Ed25519KeyPair:
    return Ed25519KeyPair.generate()


def _did_document(kp: Ed25519KeyPair) -> bytes:
    doc = DIDDocument(
        id=PROVIDER_DID,
        verificationMethod=[
            VerificationMethod(
                id=f"{PROVIDER_DID}#key-1",
                type="JsonWebKey2020",
                controller=PROVIDER_DID,
                publicKeyJwk=kp.public_jwk(),
            )
        ],
        authentication=[f"{PROVIDER_DID}#key-1"],
        assertionMethod=[f"{PROVIDER_DID}#key-1"],
    )
    return doc.model_dump_json(by_alias=True).encode()


def _record_for(name: str = "alice", subject_did: str = "did:key:z6MkAlice") -> SNSRecord:
    return SNSRecord(
        shadowname=f"{name}@{PROVIDER_HOST}",
        did=subject_did,
        endpoint=f"https://shadow.example/u/{name}/a2a",
        publicKey=PublicKeyJWK(kty="OKP", crv="Ed25519", x="aaaa"),
        subjectType="person",
        ttl=300,
        issuedAt=int(time.time()),
        **{"shadownet:v": "0.1"},
    )


def _build_handler(provider_kp: Ed25519KeyPair, record_jwt: str | None, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/did.json":
            return httpx.Response(200, content=_did_document(provider_kp))
        if request.url.path == "/.well-known/sns/v1/resolve":
            if record_jwt is None:
                return httpx.Response(status)
            return httpx.Response(200, content=record_jwt.encode())
        return httpx.Response(404)

    return handler


async def test_resolve_round_trip(provider_keypair) -> None:
    record = _record_for()
    record_jwt = sign_record(
        provider_key=provider_keypair, provider_did=PROVIDER_DID, record=record
    )
    transport = httpx.MockTransport(_build_handler(provider_keypair, record_jwt))
    async with httpx.AsyncClient(transport=transport) as http:
        resolver = Resolver(WebDIDResolver(http))
        client = SNSClient(http, resolver=resolver)
        resolved = await client.resolve("alice@x.example")
    assert resolved.did == record.did
    assert resolved.endpoint == record.endpoint


async def test_resolve_caches_until_ttl(provider_keypair) -> None:
    record = _record_for()
    record_jwt = sign_record(
        provider_key=provider_keypair, provider_did=PROVIDER_DID, record=record
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/did.json":
            return httpx.Response(200, content=_did_document(provider_keypair))
        calls["n"] += 1
        return httpx.Response(200, content=record_jwt.encode())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        clock = {"t": 0.0}
        resolver = Resolver(WebDIDResolver(http, clock=lambda: clock["t"]))
        client = SNSClient(http, resolver=resolver, clock=lambda: clock["t"])
        await client.resolve("alice@x.example")
        await client.resolve("alice@x.example")
        assert calls["n"] == 1
        clock["t"] = record.ttl + 10
        await client.resolve("alice@x.example")
        assert calls["n"] == 2


async def test_resolve_404_raises_not_found(provider_keypair) -> None:
    transport = httpx.MockTransport(_build_handler(provider_keypair, None, status=404))
    async with httpx.AsyncClient(transport=transport) as http:
        resolver = Resolver(WebDIDResolver(http))
        client = SNSClient(http, resolver=resolver)
        with pytest.raises(ShadownameNotFound):
            await client.resolve("nobody@x.example")


async def test_resolve_410_raises_tombstoned(provider_keypair) -> None:
    transport = httpx.MockTransport(_build_handler(provider_keypair, None, status=410))
    async with httpx.AsyncClient(transport=transport) as http:
        resolver = Resolver(WebDIDResolver(http))
        client = SNSClient(http, resolver=resolver)
        with pytest.raises(ShadownameTombstoned):
            await client.resolve("rip@x.example")


async def test_resolve_negative_cache(provider_keypair) -> None:
    """A 404 response is cached for the negative TTL window."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/sns/v1/resolve":
            calls["n"] += 1
            return httpx.Response(404)
        return httpx.Response(200, content=_did_document(provider_keypair))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        clock = {"t": 0.0}
        resolver = Resolver(WebDIDResolver(http))
        client = SNSClient(http, resolver=resolver, clock=lambda: clock["t"])
        with pytest.raises(ShadownameNotFound):
            await client.resolve("nobody@x.example")
        with pytest.raises(ShadownameNotFound):
            await client.resolve("nobody@x.example")
        assert calls["n"] == 1


async def test_resolve_rejects_record_with_wrong_shadowname(provider_keypair) -> None:
    record = _record_for(name="bob")  # but we'll resolve "alice"
    record_jwt = sign_record(
        provider_key=provider_keypair, provider_did=PROVIDER_DID, record=record
    )
    transport = httpx.MockTransport(_build_handler(provider_keypair, record_jwt))
    async with httpx.AsyncClient(transport=transport) as http:
        resolver = Resolver(WebDIDResolver(http))
        client = SNSClient(http, resolver=resolver)
        with pytest.raises(Exception):  # noqa: B017
            await client.resolve("alice@x.example")
