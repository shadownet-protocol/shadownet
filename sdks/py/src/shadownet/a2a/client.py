from __future__ import annotations

from typing import TYPE_CHECKING

from shadownet.a2a.session import mint_session_token

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shadownet.crypto.ed25519 import Ed25519KeyPair

# RFC-0006 §Handshake — outbound side.
#
# Shadownet does not own the A2A wire transport. This module provides:
#   1. `build_handshake_headers(...)` — synchronous helper to construct
#      `Authorization` + `X-Shadownet-Presentation` headers for one outbound
#      A2A request.
#   2. `make_handshake_event_hook(...)` — an httpx async event-hook factory.
#      Attach it to an `httpx.AsyncClient(event_hooks={"request": [...]})`
#      so every outbound A2A call automatically carries the headers, with the
#      VP cached and refreshed by the supplied callback.

__all__ = [
    "PresentationProvider",
    "build_handshake_headers",
    "make_handshake_event_hook",
]


PresentationProvider = "Callable[[str], Awaitable[str | None]]"


def build_handshake_headers(
    *,
    holder_key: Ed25519KeyPair,
    holder_did: str,
    audience_did: str,
    presentation_jwt: str | None = None,
    session_token_ttl_seconds: int | None = None,
) -> dict[str, str]:
    """Build ``Authorization`` + ``X-Shadownet-Presentation`` headers for one A2A request.

    Per RFC-0006 §Re-presentation, ``presentation_jwt`` MAY be omitted on
    follow-up requests within the verifier's freshness window. Callers are
    responsible for caching the VP and deciding when to re-mint.
    """
    token_kwargs: dict[str, object] = {
        "holder_key": holder_key,
        "holder_did": holder_did,
        "audience_did": audience_did,
    }
    if session_token_ttl_seconds is not None:
        token_kwargs["ttl_seconds"] = session_token_ttl_seconds
    session = mint_session_token(**token_kwargs)  # type: ignore[arg-type]
    headers = {"Authorization": f"Bearer {session}"}
    if presentation_jwt is not None:
        headers["X-Shadownet-Presentation"] = presentation_jwt
    return headers


def make_handshake_event_hook(
    *,
    holder_key: Ed25519KeyPair,
    holder_did: str,
    presentation_provider: Callable[[str], Awaitable[str | None]],
    audience_for: Callable[[str], str],
):
    """Build an httpx ``request`` event hook that adds the handshake headers.

    ``audience_for(url) -> callee_did`` tells the hook which DID is the audience
    for a given outbound URL — usually derived from the SNS lookup the caller
    already did. ``presentation_provider(callee_did) -> vp_jwt | None`` returns
    a cached VP (or ``None`` if the caller wants to attach one only on the
    first call within a session).
    """

    async def _hook(request) -> None:  # type: ignore[no-untyped-def]
        callee_did = audience_for(str(request.url))
        if callee_did is None:
            return
        presentation = await presentation_provider(callee_did)
        headers = build_handshake_headers(
            holder_key=holder_key,
            holder_did=holder_did,
            audience_did=callee_did,
            presentation_jwt=presentation,
        )
        for k, v in headers.items():
            request.headers[k] = v

    return _hook
