"""Hermes lifecycle hooks: on_session_start (collect inbox count) + pre_llm_call (inject + log).

The guide is explicit that ``on_session_start`` return values are ignored
and only ``pre_llm_call`` can inject context into the user message. We
record the pending-inbox count in ``on_session_start_callback`` and consume
it from ``pre_llm_call_callback`` on the first turn.
"""

from __future__ import annotations

import logging
import os
from typing import Any

__all__ = [
    "on_session_end_callback",
    "on_session_start_callback",
    "pre_llm_call_callback",
]

_log = logging.getLogger(__name__)

# session_id → pending count (set by on_session_start, consumed by pre_llm_call).
# Cleared in on_session_end to avoid unbounded growth.
_pending_inbox: dict[str, int] = {}

# session_id → pending quarantine (invitation) count. Same lifecycle as the
# inbox counter; the two counts are reported together in pre_llm_call.
_pending_quarantine: dict[str, int] = {}

# Platform names that should NOT receive shadownet inbox injections. We
# only nudge user-facing platforms; the shadownet platform itself runs
# synthetic agent-to-agent sessions and shouldn't be told about its own
# inbox.
_SUPPRESSED_PLATFORMS = frozenset({"shadownet"})


def _fetch_pending_inbox_count() -> int:
    """Best-effort, non-blocking count of pending shadownet inbox items.

    Returns 0 on any error — the hook is a UX nicety, not a correctness
    boundary. Reads ``SHADOWNET_CONNECT_URL`` to resolve the sidecar.
    """
    connect_url = os.environ.get("SHADOWNET_CONNECT_URL", "").strip()
    if not connect_url:
        return 0
    try:
        from shadownet.connect.url import parse_connect_url
    except ImportError:
        return 0
    try:
        parsed = parse_connect_url(connect_url)
    except Exception:  # noqa: BLE001
        return 0
    if not getattr(parsed, "is_inline", False):
        return 0
    base_url = (parsed.base_url or "").rstrip("/")
    token = parsed.token
    if not base_url or not token:
        return 0

    try:
        import httpx
    except ImportError:
        return 0

    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(
                f"{base_url}/v1/account/me/social/inbox",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            resp.raise_for_status()
            body = resp.json()
    except Exception:  # noqa: BLE001
        return 0

    items = body.get("items") if isinstance(body, dict) else None
    if isinstance(items, list):
        return len(items)
    count = body.get("count") if isinstance(body, dict) else None
    if isinstance(count, int):
        return count
    return 0


def _fetch_pending_quarantine_count() -> int:
    """Best-effort count of pending quarantined invitations.

    Mirrors :func:`_fetch_pending_inbox_count`. Reads from the sidecar's
    REST companion (``/v1/account/me/social/quarantine``) so we don't have
    to open an MCP session for a UX nudge.
    """
    connect_url = os.environ.get("SHADOWNET_CONNECT_URL", "").strip()
    if not connect_url:
        return 0
    try:
        from shadownet.connect.url import parse_connect_url
    except ImportError:
        return 0
    try:
        parsed = parse_connect_url(connect_url)
    except Exception:  # noqa: BLE001
        return 0
    if not getattr(parsed, "is_inline", False):
        return 0
    base_url = (parsed.base_url or "").rstrip("/")
    token = parsed.token
    if not base_url or not token:
        return 0
    try:
        import httpx
    except ImportError:
        return 0
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(
                f"{base_url}/v1/account/me/social/quarantine",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            # If the sidecar predates the quarantine surface, we get a 404 —
            # silently report zero rather than failing the session-start hook.
            if resp.status_code == 404:
                return 0
            resp.raise_for_status()
            body = resp.json()
    except Exception:  # noqa: BLE001
        return 0
    items = body.get("items") if isinstance(body, dict) else None
    if isinstance(items, list):
        return len(items)
    count = body.get("count") if isinstance(body, dict) else None
    if isinstance(count, int):
        return count
    return 0


def on_session_start_callback(
    session_id: str = "",
    model: str = "",
    platform: str = "",
    **kwargs: Any,
) -> None:
    """Populate ``_pending_inbox[session_id]`` so the first ``pre_llm_call`` injects.

    The guide states this hook's return value is ignored, so injection
    has to happen from ``pre_llm_call``. We do the cheap HTTP fetch here
    once per session and stash the result.
    """
    if platform in _SUPPRESSED_PLATFORMS:
        return
    if not session_id:
        return
    try:
        count = _fetch_pending_inbox_count()
    except Exception:  # noqa: BLE001
        _log.debug("shadownet plugin: inbox check failed silently for session %s", session_id)
        return
    if count > 0:
        _pending_inbox[session_id] = count
    try:
        quarantine = _fetch_pending_quarantine_count()
    except Exception:  # noqa: BLE001
        _log.debug(
            "shadownet plugin: quarantine check failed silently for session %s",
            session_id,
        )
        return
    if quarantine > 0:
        _pending_quarantine[session_id] = quarantine


def pre_llm_call_callback(
    session_id: str = "",
    user_message: str = "",
    conversation_history: list[Any] | None = None,
    is_first_turn: bool = False,
    model: str = "",
    platform: str = "",
    **kwargs: Any,
) -> dict[str, str] | None:
    """Inject pending-inbox context on the first turn; observe other turns.

    Per the guide, returning ``{"context": "..."}`` appends to the user
    message for this turn only. Returning ``None`` is observer-only.
    """
    _log.debug(
        "shadownet plugin: pre_llm_call session=%s model=%s platform=%s first=%s",
        session_id,
        model,
        platform,
        is_first_turn,
    )
    if not is_first_turn or platform in _SUPPRESSED_PLATFORMS:
        return None
    inbox_count = _pending_inbox.pop(session_id, 0)
    quarantine_count = _pending_quarantine.pop(session_id, 0)
    if inbox_count <= 0 and quarantine_count <= 0:
        return None
    notes: list[str] = []
    if inbox_count > 0:
        plural = "message" if inbox_count == 1 else "messages"
        notes.append(
            f"{inbox_count} pending shadownet {plural} (triage via "
            "mcp_shadownet_social_inbox_wait)"
        )
    if quarantine_count > 0:
        plural = "invitation" if quarantine_count == 1 else "invitations"
        notes.append(
            f"{quarantine_count} pending {plural} from unknown senders. "
            "Use /shadownet-invitations to review — do NOT auto-process; "
            "every accept/reject is a user decision (RFC-0006 §Cost guarantee)"
        )
    return {"context": "[shadownet] " + "; ".join(notes) + "."}


def _clear_session(session_id: str) -> None:
    _pending_inbox.pop(session_id, None)
    _pending_quarantine.pop(session_id, None)


def on_session_end_callback(
    session_id: str = "",
    completed: bool = False,
    interrupted: bool = False,
    model: str = "",
    platform: str = "",
    **kwargs: Any,
) -> None:
    """Drop any stashed inbox / quarantine counts for ``session_id``."""
    _clear_session(session_id)
