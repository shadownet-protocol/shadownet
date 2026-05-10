"""Optional FastAPI helpers for the A2A inbound side.

Lives behind the ``shadownet[fastapi]`` extra. Imports of ``fastapi`` and
``starlette`` are deferred to module-import time — installing the extra is the
caller's signal that they want this module loaded. Importing without the extra
will raise :class:`ImportError`, not a Shadownet error, so the misconfiguration
is obvious.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from fastapi import HTTPException, Request
except ImportError as exc:  # pragma: no cover - only triggers without the extra
    raise ImportError(
        "shadownet.a2a.fastapi requires `fastapi` — install the `[fastapi]` extra"
    ) from exc

from shadownet.a2a.errors import A2AError, PresentationRequiredError
from shadownet.a2a.server import HandshakeContext, verify_handshake

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from shadownet.did.resolver import Resolver
    from shadownet.sca.predicate import RequiredLevelPredicate
    from shadownet.trust import TrustStore
    from shadownet.vc.presentation import VerifiedPresentation
    from shadownet.vc.status_list import StatusListClient

__all__ = ["require_handshake"]


def require_handshake(
    *,
    expected_audience: str,
    resolver: Resolver,
    trust_store: TrustStore | None = None,
    status_list_client: StatusListClient | None = None,
    required_predicate: RequiredLevelPredicate | None = None,
    cached_presentations: dict[str, VerifiedPresentation] | None = None,
    freshness_window_seconds: int = 24 * 3600,
) -> Callable[[Request], Awaitable[HandshakeContext]]:
    """FastAPI dependency that validates the Shadownet handshake on the request.

    On success the dependency returns a :class:`HandshakeContext`. On failure
    it raises a FastAPI :class:`HTTPException` with the body shape the spec
    requires (``error``, ``detail``, ``shadownet:v``; plus ``nonce`` for
    ``presentation_required``).

    Configure once at app/router scope; treat the returned dependency as opaque.
    """
    # PresentationRequiredError already includes its nonce in the body via
    # to_response(); the catch is purely about translating to HTTPException.
    _ = PresentationRequiredError

    async def _dep(request: Request) -> HandshakeContext:
        try:
            return await verify_handshake(
                dict(request.headers),
                expected_audience=expected_audience,
                resolver=resolver,
                trust_store=trust_store,
                status_list_client=status_list_client,
                required_predicate=required_predicate,
                cached_presentations=cached_presentations,
                freshness_window_seconds=freshness_window_seconds,
            )
        except A2AError as exc:
            status, body = exc.to_response()
            raise HTTPException(status_code=status, detail=body) from exc

    return _dep
