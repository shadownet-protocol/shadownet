"""Client-side helpers for the RFC-0008 handoff redemption flow.

``redeem_handoff`` posts a single-use code to
``<base>/v1/account/connect/handoff/<code>`` and returns the embedded
bearer token. ``redeem_connect_url`` parses any ``shadownet://connect``
URL and returns ``(base_url, token)``, transparently handling both inline
and handoff forms. When a :class:`~shadownet.connect.tokens.TokenStore`
is provided, the helper consults it before contacting the server — so a
plugin can call ``redeem_connect_url`` on every start without burning
the single-use code each time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from shadownet.connect.errors import ConnectError
from shadownet.connect.url import parse_connect_url

if TYPE_CHECKING:
    from shadownet.connect.tokens import TokenStore

__all__ = ["HandoffRedemptionError", "redeem_connect_url", "redeem_handoff"]


class HandoffRedemptionError(ConnectError):
    """The handoff code could not be redeemed (consumed, expired, transport)."""


async def redeem_handoff(http: httpx.AsyncClient, *, base_url: str, code: str) -> str:
    """Trade a handoff code for a bearer token.

    Wraps ``POST <base>/v1/account/connect/handoff/<code>``. The
    response body is the RFC-0008 shape ``{"shadownet:v", "token", "expires_in"}``;
    we extract ``token`` and surface anything else as
    :class:`HandoffRedemptionError`.
    """
    url = f"{base_url.rstrip('/')}/v1/account/connect/handoff/{code}"
    try:
        resp = await http.post(url)
    except httpx.HTTPError as exc:
        raise HandoffRedemptionError(f"could not reach {url}: {exc}") from exc

    if resp.status_code == 404:
        detail = _safe_detail(resp)
        raise HandoffRedemptionError(
            f"handoff code rejected (404 {detail or 'handoff_invalid_or_expired'}). "
            "Codes are single-use; if this is your second redemption, the "
            "first consumed it. Mint a fresh connect URL on the dashboard."
        )
    if resp.status_code >= 400:
        raise HandoffRedemptionError(
            f"handoff redemption failed: HTTP {resp.status_code} {resp.text[:200]}"
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise HandoffRedemptionError("handoff response is not JSON") from exc
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise HandoffRedemptionError("handoff response missing 'token' field")
    return token


async def redeem_connect_url(
    http: httpx.AsyncClient,
    connect_url: str,
    *,
    store: TokenStore | None = None,
) -> tuple[str, str]:
    """Resolve a ``shadownet://connect`` URL to ``(base_url, token)``.

    Inline URLs return their embedded token directly. Handoff URLs check
    ``store`` first (if provided), then call
    :func:`redeem_handoff` and persist the result. Without a ``store``,
    handoff URLs are redeemed every call — appropriate only for one-shot
    flows.
    """
    parsed = parse_connect_url(connect_url)
    if parsed.is_inline:
        assert parsed.token is not None
        return parsed.base_url, parsed.token

    assert parsed.is_handoff
    assert parsed.handoff is not None
    if store is not None:
        cached = store.load(connect_url)
        if cached:
            return parsed.base_url, cached

    token = await redeem_handoff(http, base_url=parsed.base_url, code=parsed.handoff)
    if store is not None:
        store.save(connect_url, token)
    return parsed.base_url, token


def _safe_detail(resp: httpx.Response) -> str | None:
    try:
        data = resp.json()
    except ValueError:
        return None
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, dict):
            return detail.get("error")
        if isinstance(detail, str):
            return detail
        err = data.get("error")
        if isinstance(err, str):
            return err
    return None
