from __future__ import annotations

import json

import httpx
import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.errors import (
    DIDDocumentTooLarge,
    DIDMethodUnsupported,
    DIDNotResolvable,
    DIDSyntaxError,
)
from shadownet.did.resolver import Resolver
from shadownet.did.web import WebDIDResolver, parse_did_web


def _document_payload(did: str) -> bytes:
    kp = Ed25519KeyPair.generate()
    return json.dumps(
        {
            "id": did,
            "verificationMethod": [
                {
                    "id": f"{did}#key-1",
                    "type": "JsonWebKey2020",
                    "controller": did,
                    "publicKeyJwk": kp.public_jwk(),
                }
            ],
            "authentication": [f"{did}#key-1"],
            "assertionMethod": [f"{did}#key-1"],
        }
    ).encode()


@pytest.mark.parametrize(
    ("did", "url"),
    [
        ("did:web:example.com", "https://example.com/.well-known/did.json"),
        (
            "did:web:sca.sh4dow.org",
            "https://sca.sh4dow.org/.well-known/did.json",
        ),
        ("did:web:example.com:user:alice", "https://example.com/user/alice/did.json"),
    ],
)
def test_parse_did_web(did: str, url: str) -> None:
    assert parse_did_web(did) == url


def test_parse_did_web_rejects_non_web() -> None:
    with pytest.raises(DIDSyntaxError):
        parse_did_web("did:key:z6Mk...")


async def test_resolver_caches_response() -> None:
    did = "did:web:example.com"
    payload = _document_payload(did)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            content=payload,
            headers={"cache-control": "max-age=60"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        clock = {"t": 0.0}
        resolver = WebDIDResolver(http, clock=lambda: clock["t"])
        a = await resolver.resolve(did)
        b = await resolver.resolve(did)
        assert a == b
        assert calls["n"] == 1
        clock["t"] = 120.0
        await resolver.resolve(did)
        assert calls["n"] == 2


async def test_resolver_rejects_id_mismatch() -> None:
    bad_payload = _document_payload("did:web:other.example")
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=bad_payload))
    async with httpx.AsyncClient(transport=transport) as http:
        resolver = WebDIDResolver(http)
        with pytest.raises(DIDNotResolvable):
            await resolver.resolve("did:web:example.com")


async def test_resolver_rejects_oversized() -> None:
    big = b"{" + b"x" * (16 * 1024) + b"}"
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=big))
    async with httpx.AsyncClient(transport=transport) as http:
        resolver = WebDIDResolver(http)
        with pytest.raises(DIDDocumentTooLarge):
            await resolver.resolve("did:web:example.com")


async def test_resolver_rejects_extra_fields() -> None:
    did = "did:web:example.com"
    payload = json.dumps(
        {
            "id": did,
            "verificationMethod": [],
            "authentication": [],
            "assertionMethod": [],
            "service": [{"id": "x", "type": "y", "serviceEndpoint": "z"}],
        }
    ).encode()
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=payload))
    async with httpx.AsyncClient(transport=transport) as http:
        resolver = WebDIDResolver(http)
        with pytest.raises(Exception):  # noqa: B017 -- pydantic ValidationError
            await resolver.resolve(did)


async def test_dispatcher_routes_to_web() -> None:
    did = "did:web:example.com"
    payload = _document_payload(did)
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=payload))
    async with httpx.AsyncClient(transport=transport) as http:
        resolver = Resolver(WebDIDResolver(http))
        doc = await resolver.resolve(did)
        assert doc.id == did


async def test_dispatcher_resolves_did_key_locally() -> None:
    kp = Ed25519KeyPair.generate()
    from shadownet.did.key import derive_did_key

    did = derive_did_key(kp.public_bytes)
    resolver = Resolver(web=None)
    doc = await resolver.resolve(did)
    assert doc.id == did


async def test_dispatcher_rejects_unknown_method() -> None:
    resolver = Resolver()
    with pytest.raises(DIDMethodUnsupported):
        await resolver.resolve("did:dht:somehing")


async def test_dispatcher_rejects_did_web_without_resolver() -> None:
    resolver = Resolver(web=None)
    with pytest.raises(DIDMethodUnsupported):
        await resolver.resolve("did:web:example.com")
