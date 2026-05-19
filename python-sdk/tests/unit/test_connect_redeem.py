from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

if TYPE_CHECKING:
    from pathlib import Path

from shadownet.connect.errors import ConnectURLInvalid
from shadownet.connect.redeem import (
    HandoffRedemptionError,
    redeem_connect_url,
    redeem_handoff,
)
from shadownet.connect.tokens import FileTokenStore

BASE = "https://app.example"
HANDOFF = "8K3J9-W2L1Q-Y5R7T"
INLINE_URL = f"shadownet://connect?base={BASE}&token=tok-inline"
HANDOFF_URL = f"shadownet://connect?base={BASE}&handoff={HANDOFF}"


def _redeem_handler_returning(
    *,
    status: int = 200,
    json: dict[str, object] | None = None,
    raise_exc: Exception | None = None,
    expected_path: str = f"/v1/account/connect/handoff/{HANDOFF}",
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if raise_exc is not None:
            raise raise_exc
        assert request.method == "POST"
        assert request.url.path == expected_path
        return httpx.Response(status, json=json)

    return httpx.MockTransport(handler)


async def test_redeem_handoff_success() -> None:
    transport = _redeem_handler_returning(
        json={"shadownet:v": "0.1", "token": "tok-real", "expires_in": 600}
    )
    async with httpx.AsyncClient(transport=transport) as http:
        token = await redeem_handoff(http, base_url=BASE, code=HANDOFF)
    assert token == "tok-real"


async def test_redeem_handoff_404_consumed() -> None:
    """A consumed/expired code 404s with the documented error envelope."""
    transport = _redeem_handler_returning(
        status=404,
        json={"detail": {"error": "handoff_invalid_or_expired", "shadownet:v": "0.1"}},
    )
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(HandoffRedemptionError, match="single-use"):
            await redeem_handoff(http, base_url=BASE, code=HANDOFF)


async def test_redeem_handoff_5xx() -> None:
    transport = _redeem_handler_returning(status=503, json={"error": "down"})
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(HandoffRedemptionError, match="503"):
            await redeem_handoff(http, base_url=BASE, code=HANDOFF)


async def test_redeem_handoff_network_error() -> None:
    transport = _redeem_handler_returning(raise_exc=httpx.ConnectError("connection refused"))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(HandoffRedemptionError, match="could not reach"):
            await redeem_handoff(http, base_url=BASE, code=HANDOFF)


async def test_redeem_handoff_missing_token_field() -> None:
    transport = _redeem_handler_returning(json={"shadownet:v": "0.1"})
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(HandoffRedemptionError, match="missing 'token'"):
            await redeem_handoff(http, base_url=BASE, code=HANDOFF)


async def test_redeem_connect_url_inline_is_immediate() -> None:
    """No HTTP call needed for inline URLs."""

    def never_called(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("inline URL must not contact the server")

    async with httpx.AsyncClient(transport=httpx.MockTransport(never_called)) as http:
        base, token = await redeem_connect_url(http, INLINE_URL)
    assert base == BASE
    assert token == "tok-inline"


async def test_redeem_connect_url_handoff_without_store_redeems() -> None:
    transport = _redeem_handler_returning(
        json={"shadownet:v": "0.1", "token": "tok-real", "expires_in": 600}
    )
    async with httpx.AsyncClient(transport=transport) as http:
        base, token = await redeem_connect_url(http, HANDOFF_URL)
    assert base == BASE
    assert token == "tok-real"


async def test_redeem_connect_url_handoff_uses_cache_on_second_call(
    tmp_path: Path,
) -> None:
    """First call redeems and saves; second call returns cached token without HTTP."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            200, json={"shadownet:v": "0.1", "token": "tok-real", "expires_in": 600}
        )

    store = FileTokenStore(root=tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        base1, tok1 = await redeem_connect_url(http, HANDOFF_URL, store=store)
        base2, tok2 = await redeem_connect_url(http, HANDOFF_URL, store=store)

    assert (base1, tok1) == (BASE, "tok-real")
    assert (base2, tok2) == (BASE, "tok-real")
    assert call_count == 1, "second redeem_connect_url call should hit the cache"


async def test_redeem_connect_url_cache_miss_then_404_surfaces_clear_error(
    tmp_path: Path,
) -> None:
    """If cache is empty (lost) and the code is already consumed, surface guidance."""
    transport = _redeem_handler_returning(
        status=404,
        json={"detail": {"error": "handoff_invalid_or_expired"}},
    )
    store = FileTokenStore(root=tmp_path)
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(HandoffRedemptionError, match="Mint a fresh"):
            await redeem_connect_url(http, HANDOFF_URL, store=store)


async def test_redeem_connect_url_rejects_malformed_url() -> None:
    transport = _redeem_handler_returning()
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(ConnectURLInvalid):
            await redeem_connect_url(http, "shadownet://wrong-host?base=x&token=y")
