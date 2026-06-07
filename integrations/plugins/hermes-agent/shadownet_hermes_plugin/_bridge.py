"""Channel-bridge tools that let the user's chat-session agent steer background exchanges.

Registered via ``ctx.register_tool`` so they are callable from any session. The
handlers reach the process-wide :func:`._engine.get_engine` singleton the platform
adapter also uses: a standing directive set here is injected into the matching
autonomous turns, and the concierge can read the live exchange registry.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from shadownet_hermes_plugin._engine import get_engine

__all__ = ["register_bridge_tools"]

_log = logging.getLogger(__name__)
_TOOLSET = "messaging"

_DIRECTIVE_SCHEMA = {
    "description": (
        "Set or clear a standing instruction the background Shadownet agent honors. "
        "scope 'global' applies to every exchange, 'contact' to one contact's exchanges, "
        "'session' to one contextId. Empty text clears it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["global", "contact", "session"]},
            "target": {
                "type": "string",
                "description": "Contact shadowname (scope=contact) or contextId (scope=session).",
            },
            "text": {"type": "string", "description": "The instruction; empty clears it."},
        },
        "required": ["scope"],
    },
}

_EXCHANGES_SCHEMA = {
    "description": "List the live background Shadownet exchanges (contact, contextId, turns, status).",
    "parameters": {"type": "object", "properties": {}},
}

_CONTROL_SCHEMA = {
    "description": "Pause, resume, or stop a background Shadownet exchange by contextId.",
    "parameters": {
        "type": "object",
        "properties": {
            "context_id": {"type": "string"},
            "action": {"type": "string", "enum": ["pause", "resume", "stop"]},
        },
        "required": ["context_id", "action"],
    },
}

_DELEGATE_SCHEMA = {
    "description": (
        "Hand a conversation to a contact's background Shadownet exchange to run on its "
        "own. Use this instead of messaging a contact yourself — the background agent makes "
        "every move and keeps the user posted. Pass the user's intent as the instruction "
        "(e.g. 'play a word game until 3 funny sentences; keep me posted')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "contact": {"type": "string", "description": "Contact shadowname (name@host)."},
            "instruction": {"type": "string", "description": "What to do, in the user's intent."},
        },
        "required": ["contact", "instruction"],
    },
}


def _directive(args: dict[str, Any], **_: Any) -> str:
    scope = str(args.get("scope") or "")
    target = str(args.get("target") or "")
    text = str(args.get("text") or "")
    if scope in ("contact", "session") and not target:
        return f"error: scope={scope} requires a target"
    try:
        get_engine().set_directive(scope=scope, text=text, target=target)
    except ValueError as exc:
        return f"error: {exc}"
    verb = "set" if text else "cleared"
    where = scope if scope == "global" else f"{scope} {target}"
    return f"{verb} {where} directive"


def _exchanges(args: dict[str, Any], **_: Any) -> str:
    return json.dumps(get_engine().active())


def _control(args: dict[str, Any], **_: Any) -> str:
    context_id = str(args.get("context_id") or "")
    action = str(args.get("action") or "")
    if not context_id:
        return "error: context_id is required"
    engine = get_engine()
    if action == "pause":
        ok = engine.set_status(context_id, "paused")
    elif action == "resume":
        ok = engine.set_status(context_id, "active")
    elif action == "stop":
        ok = engine.end(context_id)
    else:
        return f"error: unknown action {action!r}"
    return f"{action} {context_id}" if ok else f"no exchange for {context_id}"


def _delegate(args: dict[str, Any], **_: Any) -> str:
    contact = str(args.get("contact") or "")
    instruction = str(args.get("instruction") or "")
    if not contact or not instruction:
        return "error: contact and instruction are required"
    engine = get_engine()
    ctx = engine.context_for_contact(contact)
    target, where = (ctx, f"thread {ctx}") if ctx else (contact, "a new thread")
    # One-shot: the instruction rides into the opening turn via the kickoff inject and
    # then lives in the session's history. Persisting it as a standing directive would
    # re-arm it on every future turn with the contact (a perpetual-exchange bug).
    engine.request_kickoff(target=target, contact=contact, instruction=instruction)
    return f"delegated to {contact}'s background exchange ({where}); it will handle it and keep you posted"


_TOOLS = (
    ("shadownet_directive", _DIRECTIVE_SCHEMA, _directive),
    ("shadownet_exchanges", _EXCHANGES_SCHEMA, _exchanges),
    ("shadownet_exchange_control", _CONTROL_SCHEMA, _control),
    ("shadownet_delegate", _DELEGATE_SCHEMA, _delegate),
)


def register_bridge_tools(ctx: Any) -> int:
    """Register the channel-bridge tools; returns the number registered."""
    register = getattr(ctx, "register_tool", None)
    if register is None:
        return 0
    count = 0
    for name, schema, handler in _TOOLS:
        try:
            register(name=name, toolset=_TOOLSET, schema=schema, handler=handler)
        except Exception as exc:  # noqa: BLE001
            _log.warning("shadownet plugin: failed to register tool %s: %s", name, exc)
            continue
        count += 1
    return count
