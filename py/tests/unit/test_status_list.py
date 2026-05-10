from __future__ import annotations

import json

import httpx
import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.crypto.jwt import sign_jwt
from shadownet.vc.errors import Revoked, StatusListUnavailable
from shadownet.vc.status_list import BitstringStatusList, StatusListClient, encode_bitstring


def _build_status_credential(bits: bytes) -> str:
    encoded = encode_bitstring(bits)
    payload = {
        "iss": "did:web:sca.example",
        "iat": 1759200000,
        "exp": 1759286400,
        "vc": {
            "@context": ["https://www.w3.org/ns/credentials/v2"],
            "type": ["VerifiableCredential", "BitstringStatusListCredential"],
            "credentialSubject": {
                "id": "https://sca.example/status/2026-q3",
                "type": "BitstringStatusList",
                "statusPurpose": "revocation",
                "encodedList": encoded,
            },
        },
    }
    kp = Ed25519KeyPair.generate()
    return sign_jwt(payload, kp)


def _bits_with_index_set(index: int, total_bits: int = 64) -> bytes:
    raw = bytearray(total_bits // 8)
    byte_i, bit_i = divmod(index, 8)
    raw[byte_i] |= 1 << (7 - bit_i)
    return bytes(raw)


def test_bitstring_round_trip() -> None:
    bits = _bits_with_index_set(5)
    sl_jwt = _build_status_credential(bits)
    sl = BitstringStatusList.from_credential(sl_jwt)
    assert sl.is_set(5)
    assert not sl.is_set(0)
    assert not sl.is_set(6)
    assert not sl.is_set(99999)  # past list = not revoked


async def test_status_client_caches_until_ttl() -> None:
    sl_jwt = _build_status_credential(_bits_with_index_set(3))
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=sl_jwt, headers={"cache-control": "max-age=10"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        clock = {"t": 0.0}
        client = StatusListClient(http, clock=lambda: clock["t"])
        sl = await client.fetch("https://sca.example/status/x")
        assert sl.is_set(3)
        await client.fetch("https://sca.example/status/x")
        assert calls["n"] == 1
        clock["t"] = 30.0
        await client.fetch("https://sca.example/status/x")
        assert calls["n"] == 2


async def test_check_not_revoked_raises_when_set() -> None:
    sl_jwt = _build_status_credential(_bits_with_index_set(7))
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=sl_jwt))
    async with httpx.AsyncClient(transport=transport) as http:
        client = StatusListClient(http)
        with pytest.raises(Revoked):
            await client.check_not_revoked("https://sca.example/status/x", 7, fail_closed=True)
        # Different index is fine.
        await client.check_not_revoked("https://sca.example/status/x", 8, fail_closed=True)


async def test_fetch_failure_failclosed() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as http:
        client = StatusListClient(http)
        with pytest.raises(StatusListUnavailable):
            await client.check_not_revoked("https://sca.example/status/x", 0, fail_closed=True)


async def test_fetch_failure_open_for_l1() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(404))
    async with httpx.AsyncClient(transport=transport) as http:
        client = StatusListClient(http)
        # fail_closed=False — does not raise, treated as "not revoked".
        await client.check_not_revoked("https://sca.example/status/x", 0, fail_closed=False)


def test_status_list_rejects_non_credential() -> None:
    bad = json.dumps({"foo": "bar"}).encode()
    with pytest.raises(StatusListUnavailable):
        BitstringStatusList.from_credential(bad.decode())
