"""Hermes lifecycle hooks: on_session_start (collect inbox count) + pre_llm_call (inject + log).

The guide is explicit that ``on_session_start`` return values are ignored
and only ``pre_llm_call`` can inject context into the user message. We
record the pending-inbox count in ``on_session_start_callback`` and consume
it from ``pre_llm_call_callback`` on the first turn.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

__all__ = [
    "on_session_end_callback",
    "on_session_start_callback",
    "pre_llm_call_callback",
    "pre_tool_call_callback",
]

_log = logging.getLogger(__name__)

# session_id → count of strangers awaiting review (set by on_session_start,
# consumed once by pre_llm_call). Cleared in on_session_end to avoid growth.
# Known contacts are handled autonomously, so they are deliberately NOT counted.
_pending_inbox: dict[str, int] = {}

# Exchange-DRIVING MCP tools the foreground must not call directly: it delegates
# via shadownet_delegate instead, so the agentic loop lives in the background
# session, not the user's chat. The background (shadownet platform) keeps them —
# the coordinate flow uses the typed ones. The inbox SNAPSHOT (mcp_shadownet_inbox)
# is left available for stranger triage; only the long-poll is blocked.
_FOREGROUND_BLOCKED_TOOLS = frozenset(
    {
        "mcp_shadownet_send",
        "mcp_shadownet_respond",
        "mcp_shadownet_inbox_wait",
        "mcp_shadownet_coordinate",
        "mcp_shadownet_confirm_plan",
        "mcp_shadownet_accept_plan",
    }
)

_DELEGATE_HINT = (
    "Don't call Shadownet MCP directly from your chat with the user. To message or "
    "run a conversation with a contact, use shadownet_delegate(contact, instruction) — "
    "the background exchange makes the moves and keeps the user posted. To see what's "
    "happening, use shadownet_exchanges."
)

# Platform names that should NOT receive shadownet inbox injections. We
# only nudge user-facing platforms; the shadownet platform itself runs
# synthetic agent-to-agent sessions and shouldn't be told about its own
# inbox.
_SUPPRESSED_PLATFORMS = frozenset({"shadownet"})


def _resolve_mcp_target() -> tuple[str, str] | None:
    """Resolve ``(mcp_endpoint, bearer_token)`` from the configured connect URI.

    Returns ``None`` on any failure (URI missing, handoff form, SDK absent,
    parse error). The hook is best-effort UX, not a correctness boundary.
    """
    connect_uri = os.environ.get("SHADOWNET_CONNECT_URL", "").strip()
    if not connect_uri:
        return None
    try:
        from shadownet.onboarding import parse_connect_uri
    except ImportError:
        return None
    try:
        parsed = parse_connect_uri(connect_uri)
    except Exception:  # noqa: BLE001
        return None
    if not parsed.is_inline:
        # Handoff URIs require an out-of-band redemption step; we don't run
        # that from this hook. Skip silently.
        return None
    if not parsed.mcp_endpoint or not parsed.access_token:
        return None
    return parsed.mcp_endpoint, parsed.access_token


async def _fetch_inbox_count_async(endpoint: str, token: str) -> int:
    """Open a brief MCP session and count strangers awaiting the user's review.

    Known contacts are handled autonomously, so the only inbox items the human
    needs nudging about are those held in ``stranger_review`` (which the inbox
    tool only returns when ``includeReview`` is set).
    """
    try:
        from shadownet.mcp import InboxInput, ShadownetMCPClient
    except ImportError:
        return 0
    try:
        async with ShadownetMCPClient.connect(endpoint=endpoint, access_token=token) as client:
            result = await client.inbox(InboxInput(includeReview=True, limit=50))
    except Exception:  # noqa: BLE001
        return 0
    return sum(1 for item in result.items if getattr(item, "status", "") == "stranger_review")


def _fetch_pending_inbox_count() -> int:
    """Best-effort, non-blocking count of pending shadownet inbox items.

    Returns 0 on any error — the hook is a UX nicety, not a correctness
    boundary. Runs the async MCP call on a private event loop so the host
    LLM's own asyncio.run isn't shadowed.
    """
    target = _resolve_mcp_target()
    if target is None:
        return 0
    endpoint, token = target
    try:
        return asyncio.run(_fetch_inbox_count_async(endpoint, token))
    except Exception:  # noqa: BLE001
        return 0


def on_session_start_callback(
    session_id: str = "",
    model: str = "",
    platform: str = "",
    **kwargs: Any,
) -> None:
    """Populate ``_pending_inbox[session_id]`` so the first ``pre_llm_call`` injects.

    The guide states this hook's return value is ignored, so injection
    has to happen from ``pre_llm_call``. We do the MCP call here once per
    session and stash the result.
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


_COORDINATION_TRIGGERS = (
    "plan a",
    "set up a",
    "schedule a",
    "schedule lunch",
    "coordinate with",
    "meet up with",
    "meeting with",
    "coffee with",
    "dinner with",
    "lunch with",
    "call with",
    "grab coffee",
    "grab lunch",
    "grab dinner",
    "plan something",
    "plan an event",
    "hang out with",
)


def _looks_like_coordination(msg: str) -> bool:
    """Heuristic: does the user message look like a coordination request?"""
    lower = msg.lower()
    return any(trigger in lower for trigger in _COORDINATION_TRIGGERS)


def pre_llm_call_callback(
    session_id: str = "",
    user_message: str = "",
    conversation_history: list[Any] | None = None,
    is_first_turn: bool = False,
    model: str = "",
    platform: str = "",
    **kwargs: Any,
) -> dict[str, str] | None:
    """Inject coordination skill hint or pending-inbox context on the first turn.

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
    if platform in _SUPPRESSED_PLATFORMS:
        return None

    parts: list[str] = []

    if is_first_turn:
        count = _pending_inbox.pop(session_id, 0)
        if count > 0:
            plural = "message" if count == 1 else "messages"
            parts.append(
                f"[shadownet] {count} shadownet {plural} from strangers await the user's "
                "review. Load the shadownet-messaging skill to triage when they have a moment."
            )

    if _looks_like_coordination(user_message):
        parts.append(
            "[shadownet-coordinate] The user wants to coordinate with a contact. "
            "Load the shadownet-coordinate skill and follow it — it carries the "
            "exact intent URIs and body shapes. Do not invent your own."
        )

    return {"context": "\n\n".join(parts)} if parts else None


def _current_platform() -> str:
    """Platform of the in-flight turn, from the session contextvar (propagated to the
    tool thread); '' when unavailable (CLI/tests) so the block fails open."""
    try:
        from gateway.session_context import get_session_env

        return str(get_session_env("HERMES_SESSION_PLATFORM", "") or "")
    except Exception:  # noqa: BLE001 - no session context; fail open
        return ""


def pre_tool_call_callback(
    tool_name: str = "",
    args: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, str] | None:
    """Block raw exchange MCP tools in a foreground session; steer to delegation.

    Reads the current platform ambiently (the block-hook call site passes no session
    id). Returns ``{"action": "block", "message": ...}`` for a blocked call; a
    background shadownet turn keeps the tools (coordinate uses the typed ones), and an
    unknown platform is left alone (fail-open).
    """
    if tool_name not in _FOREGROUND_BLOCKED_TOOLS:
        return None
    platform = _current_platform()
    if platform and platform not in _SUPPRESSED_PLATFORMS:
        return {"action": "block", "message": _DELEGATE_HINT}
    return None


def on_session_end_callback(
    session_id: str = "",
    completed: bool = False,
    interrupted: bool = False,
    model: str = "",
    platform: str = "",
    **kwargs: Any,
) -> None:
    """Drop any stashed inbox count for ``session_id``."""
    _pending_inbox.pop(session_id, None)
