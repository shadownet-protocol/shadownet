from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shadownet.a2a.errors import (
    LevelInsufficientError,
    PresentationInvalidError,
    PresentationRequiredError,
)
from shadownet.a2a.session import verify_session_token

if TYPE_CHECKING:
    from collections.abc import Mapping

    from shadownet.sca.predicate import RequiredLevelPredicate
    from shadownet.trust import TrustStore
    from shadownet.vc.presentation import VerifiedPresentation
    from shadownet.vc.status_list import StatusListClient

# RFC-0006 §Handshake — inbound (verifier) side.

__all__ = [
    "HandshakeContext",
    "issue_nonce",
    "verify_handshake",
]


def issue_nonce() -> str:
    """Return a fresh 256-bit verifier nonce for the ``presentation_required`` challenge."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True, slots=True)
class HandshakeContext:
    """Result of a successful inbound A2A handshake.

    ``presentation`` is ``None`` when the caller reused a cached VP within the
    freshness window (RFC-0006 §Re-presentation). In that case the caller side
    relied on the verifier's own session cache; the caller's DID is still
    confirmed by the session-token signature.
    """

    caller_did: str
    presentation: VerifiedPresentation | None


async def verify_handshake(
    headers: Mapping[str, str],
    *,
    expected_audience: str,
    resolver,
    trust_store: TrustStore | None = None,
    status_list_client: StatusListClient | None = None,
    required_predicate: RequiredLevelPredicate | None = None,
    cached_presentations: Mapping[str, VerifiedPresentation] | None = None,
    expected_nonce: str | None = None,
    freshness_window_seconds: int = 24 * 3600,
    now: int | None = None,
) -> HandshakeContext:
    """Validate an inbound A2A request's handshake headers.

    Order of checks (RFC-0006 §Handshake):

    1. ``Authorization: Bearer <session-token>`` MUST be present and signed by
       the caller's DID, with ``aud`` = ``expected_audience``.
    2. If ``X-Shadownet-Presentation`` is present, it is verified end-to-end
       against ``trust_store`` / ``status_list_client``. If the predicate is
       supplied, it MUST be satisfied — otherwise :class:`LevelInsufficientError`.
    3. If no presentation header is present and no cached VP exists for the
       caller's DID, raise :class:`PresentationRequiredError` carrying a fresh
       nonce so the caller can retry.
    """
    normalized = _normalize_headers(headers)
    auth = normalized.get("authorization")
    if not auth or not auth.lower().startswith("bearer "):
        raise PresentationInvalidError("missing or malformed Authorization header")
    session_jwt = auth.split(None, 1)[1]
    session = await verify_session_token(
        session_jwt,
        expected_audience=expected_audience,
        resolver=resolver,
        now=now,
    )
    caller_did = session.iss

    vp_jwt = normalized.get("x-shadownet-presentation")
    if vp_jwt is None:
        if cached_presentations is not None and caller_did in cached_presentations:
            return HandshakeContext(caller_did=caller_did, presentation=None)
        raise PresentationRequiredError(nonce=issue_nonce())

    from shadownet.vc.presentation import verify_presentation

    presentation = await verify_presentation(
        vp_jwt,
        resolver=resolver,
        expected_audience=expected_audience,
        expected_nonce=expected_nonce,
        trust_store=trust_store,
        status_list_client=status_list_client,
        freshness_window_seconds=freshness_window_seconds,
        now=now,
    )
    if presentation.holder_did != caller_did:
        raise PresentationInvalidError("VP holder does not match session-token issuer")

    if required_predicate is not None:
        from shadownet.sca.predicate import evaluate_predicate

        if not evaluate_predicate(required_predicate, presentation):
            raise LevelInsufficientError(
                "presented credentials do not satisfy the required predicate"
            )

    return HandshakeContext(caller_did=caller_did, presentation=presentation)


def _normalize_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}
