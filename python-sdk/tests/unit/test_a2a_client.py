from __future__ import annotations

import httpx
import pytest

from shadownet.a2a.client import build_handshake_headers, make_handshake_event_hook
from shadownet.a2a.errors import PresentationInvalidError
from shadownet.a2a.session import verify_session_token
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.key import derive_did_key
from shadownet.did.resolver import Resolver


@pytest.fixture
def caller() -> tuple[Ed25519KeyPair, str]:
    kp = Ed25519KeyPair.generate()
    return kp, derive_did_key(kp.public_bytes)


@pytest.fixture
def callee_did() -> str:
    return derive_did_key(Ed25519KeyPair.generate().public_bytes)


async def test_build_handshake_headers_emits_session_token(caller, callee_did) -> None:
    kp, did = caller
    headers = build_handshake_headers(
        holder_key=kp,
        holder_did=did,
        audience_did=callee_did,
    )
    assert "X-Shadownet-Presentation" not in headers
    assert headers["Authorization"].startswith("Bearer ")
    token = headers["Authorization"].removeprefix("Bearer ")
    parsed = await verify_session_token(token, expected_audience=callee_did, resolver=Resolver())
    assert parsed.iss == did
    assert parsed.purpose == "a2a-session"


async def test_build_handshake_headers_attaches_presentation(caller, callee_did) -> None:
    kp, did = caller
    headers = build_handshake_headers(
        holder_key=kp,
        holder_did=did,
        audience_did=callee_did,
        presentation_jwt="some-vp-jwt",
    )
    assert headers["X-Shadownet-Presentation"] == "some-vp-jwt"


async def test_build_handshake_headers_custom_ttl(caller, callee_did) -> None:
    kp, did = caller
    headers = build_handshake_headers(
        holder_key=kp,
        holder_did=did,
        audience_did=callee_did,
        session_token_ttl_seconds=60,
    )
    token = headers["Authorization"].removeprefix("Bearer ")
    parsed = await verify_session_token(token, expected_audience=callee_did, resolver=Resolver())
    assert parsed.exp - parsed.iat == 60


async def test_build_handshake_headers_rejects_oversized_ttl(caller, callee_did) -> None:
    kp, did = caller
    with pytest.raises(ValueError):
        build_handshake_headers(
            holder_key=kp,
            holder_did=did,
            audience_did=callee_did,
            session_token_ttl_seconds=10_000,
        )


async def test_event_hook_attaches_headers_to_outbound_request(caller, callee_did) -> None:
    kp, did = caller
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200, json={"ok": True})

    async def provide_vp(audience: str) -> str | None:
        assert audience == callee_did
        return "cached-vp-jwt"

    def audience_for(url: str) -> str | None:
        return callee_did if "/a2a/" in url else None

    hook = make_handshake_event_hook(
        holder_key=kp,
        holder_did=did,
        presentation_provider=provide_vp,
        audience_for=audience_for,
    )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, event_hooks={"request": [hook]}) as http:
        await http.get("https://lukas.example/a2a/message:send")

    assert "authorization" in captured
    assert captured["x-shadownet-presentation"] == "cached-vp-jwt"
    token = captured["authorization"].removeprefix("Bearer ")
    parsed = await verify_session_token(token, expected_audience=callee_did, resolver=Resolver())
    assert parsed.iss == did


async def test_event_hook_skips_non_shadownet_traffic(caller) -> None:
    kp, did = caller
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers.get("Authorization", ""))
        return httpx.Response(200)

    async def provide_vp(audience: str) -> str | None:
        return "should-not-be-called"

    def audience_for(url: str) -> str | None:
        return None  # nothing is Shadownet-bound

    hook = make_handshake_event_hook(
        holder_key=kp,
        holder_did=did,
        presentation_provider=provide_vp,
        audience_for=audience_for,
    )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, event_hooks={"request": [hook]}) as http:
        await http.get("https://random.example/health")

    assert captured == [""]  # no Authorization header was added


async def test_event_hook_omits_presentation_when_provider_returns_none(caller, callee_did) -> None:
    """RFC-0006 §Re-presentation: VP MAY be omitted on follow-up requests."""
    kp, did = caller
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200)

    async def provide_vp(audience: str) -> str | None:
        return None  # within freshness window — no VP needed

    def audience_for(url: str) -> str | None:
        return callee_did

    hook = make_handshake_event_hook(
        holder_key=kp,
        holder_did=did,
        presentation_provider=provide_vp,
        audience_for=audience_for,
    )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, event_hooks={"request": [hook]}) as http:
        await http.get("https://lukas.example/a2a/x")

    assert "authorization" in captured
    assert "x-shadownet-presentation" not in captured


def test_session_token_oversized_ttl_message(caller, callee_did) -> None:
    """Sanity: the validation error mentions the spec cap."""
    kp, did = caller
    with pytest.raises(ValueError, match="300s"):
        build_handshake_headers(
            holder_key=kp,
            holder_did=did,
            audience_did=callee_did,
            session_token_ttl_seconds=600,
        )


def test_unrelated_module_attr_for_coverage_of_invalid_session() -> None:
    """Regression marker: PresentationInvalidError still importable from a2a.errors."""
    assert PresentationInvalidError.__name__ == "PresentationInvalidError"
