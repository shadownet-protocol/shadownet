"""Onboarding URI + handoff + refresh — RFC 0003.

Defines the SDK-facing primitives for the ``shadow://connect`` URI that a
host LLM consumes to learn the Sidecar's MCP endpoint plus its bearer
token. Two URI forms (inline + handoff), one redemption endpoint, one
refresh endpoint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import parse_qsl, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from shadownet.errors import ShadownetError

__all__ = [
    "DEFAULT_HANDOFF_TIMEOUT",
    "DEFAULT_REFRESH_TIMEOUT",
    "ConnectURI",
    "ConnectURIError",
    "HandoffError",
    "HandoffExpiredError",
    "HandoffRateLimitedError",
    "HandoffResponse",
    "HandoffUnknownError",
    "RefreshError",
    "RefreshInvalidError",
    "RefreshRateLimitedError",
    "RefreshResponse",
    "aredeem_handoff",
    "arefresh_access_token",
    "parse_connect_uri",
    "redeem_handoff",
    "refresh_access_token",
]


CONNECT_SCHEME: Final = "shadow"
CONNECT_AUTHORITY: Final = "connect"
HANDOFF_PATH: Final = "/.well-known/shadownet/onboard/handoff/"
REFRESH_PATH: Final = "/.well-known/shadownet/onboard/refresh"
DEFAULT_HANDOFF_TIMEOUT: Final = 10.0
DEFAULT_REFRESH_TIMEOUT: Final = 10.0
_HANDOFF_PATTERN: Final = re.compile(r"^[A-Za-z0-9._-]{16,128}$")
_LOOPBACK_PREFIXES: Final = (
    "http://localhost",
    "http://127.0.0.1",
    "http://[::1]",
)


class ConnectURIError(ShadownetError):
    """``shadow://connect?...`` URI is malformed or violates §3."""


class HandoffError(ShadownetError):
    """Handoff redemption failed."""


class HandoffUnknownError(HandoffError):
    """Handoff code was never issued, or has already been redeemed (HTTP 404)."""


class HandoffExpiredError(HandoffError):
    """Handoff code is past its TTL (HTTP 410)."""


class HandoffRateLimitedError(HandoffError):
    """Handoff redemption hit the rate limit (HTTP 429)."""


class RefreshError(ShadownetError):
    """Refresh token exchange failed."""


class RefreshInvalidError(RefreshError):
    """Refresh token is unknown, already rotated, or revoked (HTTP 401)."""


class RefreshRateLimitedError(RefreshError):
    """Refresh hit the rate limit (HTTP 429)."""


@dataclass(frozen=True, slots=True)
class ConnectURI:
    """Parsed ``shadow://connect`` URI per RFC 0003 §3."""

    mcp_endpoint: str
    access_token: str | None
    handoff_code: str | None

    @property
    def is_inline(self) -> bool:
        return self.access_token is not None

    @property
    def is_handoff(self) -> bool:
        return self.handoff_code is not None


class HandoffResponse(BaseModel):
    """Success body of ``POST /.well-known/shadownet/onboard/handoff/<code>``."""

    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)

    access_token: str = Field(alias="accessToken")
    expires_at: str | None = Field(default=None, alias="expiresAt")
    refresh_token: str | None = Field(default=None, alias="refreshToken")


class RefreshResponse(BaseModel):
    """Success body of ``POST /.well-known/shadownet/onboard/refresh``."""

    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)

    access_token: str = Field(alias="accessToken")
    expires_at: str | None = Field(default=None, alias="expiresAt")
    refresh_token: str | None = Field(default=None, alias="refreshToken")


def parse_connect_uri(uri: str) -> ConnectURI:
    parts = urlparse(uri)
    if parts.scheme != CONNECT_SCHEME:
        raise ConnectURIError(f"URI scheme must be {CONNECT_SCHEME!r}, got {parts.scheme!r}")
    if parts.netloc != CONNECT_AUTHORITY:
        raise ConnectURIError(f"URI authority must be {CONNECT_AUTHORITY!r}, got {parts.netloc!r}")
    if parts.path not in ("", "/"):
        raise ConnectURIError(f"URI path must be empty or '/', got {parts.path!r}")
    if parts.fragment:
        raise ConnectURIError("URI MUST NOT carry a fragment")

    raw_pairs = parse_qsl(parts.query, keep_blank_values=False, strict_parsing=True)
    seen: dict[str, str] = {}
    for key, value in raw_pairs:
        if key in seen:
            raise ConnectURIError(f"duplicate query parameter {key!r}")
        seen[key] = value

    mcp_endpoint = seen.get("mcp")
    if not mcp_endpoint:
        raise ConnectURIError("missing required parameter 'mcp'")

    if not mcp_endpoint.startswith("https://") and not mcp_endpoint.startswith(_LOOPBACK_PREFIXES):
        raise ConnectURIError(
            f"mcp endpoint must be https:// (or loopback http://), got {mcp_endpoint!r}"
        )

    access_token = seen.get("token")
    handoff_code = seen.get("handoff")
    if (access_token is None) == (handoff_code is None):
        raise ConnectURIError(
            "exactly one of 'token' or 'handoff' MUST be present (got both or neither)"
        )

    if handoff_code is not None and not _HANDOFF_PATTERN.match(handoff_code):
        raise ConnectURIError(
            f"handoff code does not match 16*128(ALPHA / DIGIT / '._-'): {handoff_code!r}"
        )

    return ConnectURI(
        mcp_endpoint=mcp_endpoint,
        access_token=access_token,
        handoff_code=handoff_code,
    )


def redeem_handoff(
    mcp_origin: str,
    code: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_HANDOFF_TIMEOUT,
) -> HandoffResponse:
    url = _handoff_url(mcp_origin, code)
    response = _post(url, json={}, client=client, timeout=timeout, error_cls=HandoffError)
    return _interpret_handoff_response(response, url)


async def aredeem_handoff(
    mcp_origin: str,
    code: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_HANDOFF_TIMEOUT,
) -> HandoffResponse:
    """Async sibling of :func:`redeem_handoff` using ``httpx.AsyncClient``."""
    url = _handoff_url(mcp_origin, code)
    response = await _apost(url, json={}, client=client, timeout=timeout, error_cls=HandoffError)
    return _interpret_handoff_response(response, url)


def refresh_access_token(
    mcp_origin: str,
    refresh_token: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_REFRESH_TIMEOUT,
) -> RefreshResponse:
    url = mcp_origin.rstrip("/") + REFRESH_PATH
    response = _post(
        url,
        json={},
        client=client,
        timeout=timeout,
        error_cls=RefreshError,
        headers={"Authorization": f"Bearer {refresh_token}"},
    )
    return _interpret_refresh_response(response, url)


async def arefresh_access_token(
    mcp_origin: str,
    refresh_token: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_REFRESH_TIMEOUT,
) -> RefreshResponse:
    """Async sibling of :func:`refresh_access_token` using ``httpx.AsyncClient``."""
    url = mcp_origin.rstrip("/") + REFRESH_PATH
    response = await _apost(
        url,
        json={},
        client=client,
        timeout=timeout,
        error_cls=RefreshError,
        headers={"Authorization": f"Bearer {refresh_token}"},
    )
    return _interpret_refresh_response(response, url)


def _handoff_url(mcp_origin: str, code: str) -> str:
    if not _HANDOFF_PATTERN.match(code):
        raise HandoffError(f"handoff code does not match the grammar: {code!r}")
    return mcp_origin.rstrip("/") + HANDOFF_PATH + code


def _interpret_handoff_response(response: httpx.Response, url: str) -> HandoffResponse:
    status = response.status_code
    if status == 200:
        body = _json_body(response, HandoffError)
        try:
            return HandoffResponse.model_validate(body)
        except Exception as exc:
            raise HandoffError(f"malformed handoff response: {exc}") from exc
    if status == 404:
        raise HandoffUnknownError(f"handoff code unknown or already redeemed: {url!r}")
    if status == 410:
        raise HandoffExpiredError(f"handoff code expired: {url!r}")
    if status == 429:
        raise HandoffRateLimitedError(f"handoff rate-limited: {url!r}")
    raise HandoffError(f"handoff redemption returned HTTP {status}")


def _interpret_refresh_response(response: httpx.Response, url: str) -> RefreshResponse:
    status = response.status_code
    if status == 200:
        body = _json_body(response, RefreshError)
        try:
            return RefreshResponse.model_validate(body)
        except Exception as exc:
            raise RefreshError(f"malformed refresh response: {exc}") from exc
    if status == 401:
        raise RefreshInvalidError(f"refresh token invalid or revoked: {url!r}")
    if status == 429:
        raise RefreshRateLimitedError(f"refresh rate-limited: {url!r}")
    raise RefreshError(f"refresh returned HTTP {status}")


def _post(
    url: str,
    *,
    json: object,
    client: httpx.Client | None,
    timeout: float,
    error_cls: type[ShadownetError],
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    owned: httpx.Client | None = None
    try:
        c = client
        if c is None:
            c = owned = httpx.Client(timeout=timeout)
        return c.post(url, json=json, headers=request_headers)
    except httpx.HTTPError as exc:
        raise error_cls(f"transport failed for {url!r}: {exc}") from exc
    finally:
        if owned is not None:
            owned.close()


async def _apost(
    url: str,
    *,
    json: object,
    client: httpx.AsyncClient | None,
    timeout: float,
    error_cls: type[ShadownetError],
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    owned: httpx.AsyncClient | None = None
    try:
        c = client
        if c is None:
            c = owned = httpx.AsyncClient(timeout=timeout)
        return await c.post(url, json=json, headers=request_headers)
    except httpx.HTTPError as exc:
        raise error_cls(f"transport failed for {url!r}: {exc}") from exc
    finally:
        if owned is not None:
            await owned.aclose()


def _json_body(response: httpx.Response, error_cls: type[ShadownetError]) -> object:
    try:
        return response.json()
    except ValueError as exc:
        raise error_cls(f"non-JSON body: {exc}") from exc
