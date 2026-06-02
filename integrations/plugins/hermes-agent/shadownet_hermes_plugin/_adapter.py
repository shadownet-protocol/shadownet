"""Hermes Agent platform adapter for Shadownet (v0.2).

This module is loaded inside a running Hermes Agent process. It MUST NOT
import Hermes types at module-import time (so the package remains
importable for testing and tooling outside a Hermes install). Hermes
types are deferred to function bodies or guarded by ``TYPE_CHECKING``.

The plugin model follows the Telegram precedent in
``gateway/platforms/telegram.py``: a per-account adapter holds a long-lived
outbound connection (here, an MCP session against the Shadownet sidecar
using ``shadownet.mcp.ShadownetMCPClient``), runs an inbox loop in an
``asyncio.Task``, and dispatches each inbound event to
``self.handle_message(MessageEvent)``.

Inbound messages from a known contact are handled autonomously by the full
Hermes agent (the engine in :mod:`._engine` gates and bounds this); free-form
messages use the shadownet-autonomous skill and coordination intents use the
shadownet-coordinate skill. Strangers are left in the sidecar for the human to
triage (pull). The agent surfaces to the human only by its own judgment, via
``send_message`` — there is no per-message push.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from shadownet.mcp import (
    BodySlot,
    ContactDetailInput,
    InboxInput,
    InboxWaitInput,
    RespondInput,
    SendInput,
    ShadownetMCPClient,
)
from shadownet.mcp.intents import (
    ACCEPT_PLAN_V1_URI,
    CONFIRM_PLAN_V1_URI,
    COORDINATE_V1_URI,
    PROPOSE_PLAN_V1_URI,
)
from shadownet.onboarding import parse_connect_uri

from shadownet_hermes_plugin._engine import ExchangeEngine

if TYPE_CHECKING:
    # Hermes Agent ships its plugin-side types under these modules. They
    # only resolve inside a Hermes install — we use TYPE_CHECKING so static
    # analysis works even when the package isn't present locally.
    from gateway.platforms.base import (
        BasePlatformAdapter,
        MessageEvent,
        SendResult,
    )
else:
    # At runtime, derive lazily inside _resolve_hermes_types(). The base
    # class can't be the literal ``BasePlatformAdapter`` import at module
    # level — that would crash module load outside Hermes.
    BasePlatformAdapter = object  # type: ignore[assignment,misc]
    MessageEvent = object  # type: ignore[assignment,misc]
    SendResult = object  # type: ignore[assignment,misc]

_log = logging.getLogger(__name__)

# Default access-token issuer host when SHADOWNET_TOKEN is set without an
# accompanying SHADOWNET_CONNECT_URL. v0.2 has no canonical hosted provider
# yet; this is just a placeholder operators can override.
DEFAULT_SIDECAR_BASE_URL = "https://app.sh4dow.org"

# Typed v0.2 coordination intents; these are handled by the shadownet-coordinate
# skill rather than the free-form shadownet-autonomous skill.
_COORDINATION_INTENTS = frozenset(
    {COORDINATE_V1_URI, PROPOSE_PLAN_V1_URI, CONFIRM_PLAN_V1_URI, ACCEPT_PLAN_V1_URI}
)


def _resolve_hermes_types() -> tuple[type, type, type]:
    """Import Hermes types lazily at first use, raising a clear error if missing."""
    try:
        from gateway.platforms.base import (
            BasePlatformAdapter as _Base,
        )
        from gateway.platforms.base import (
            MessageEvent as _MessageEvent,
        )
        from gateway.platforms.base import (
            SendResult as _SendResult,
        )
    except ImportError as exc:  # pragma: no cover — only outside Hermes
        raise RuntimeError(
            "shadownet_hermes_plugin must run inside a Hermes Agent install "
            "(gateway.platforms.base not importable). Install hermes-agent."
        ) from exc
    return _Base, _MessageEvent, _SendResult


def build_adapter_class() -> type:
    """Construct the platform adapter class binding to Hermes's real base.

    Called by ``register()`` after Hermes has loaded. Returning a freshly
    constructed class — rather than declaring ``class ShadownetAdapter(...)``
    at module top — keeps the module loadable when Hermes types aren't
    importable (development, unit tests, CI without Hermes installed).
    """
    base_adapter, message_event_cls, _ = _resolve_hermes_types()

    class ShadownetAdapter(base_adapter):  # type: ignore[misc,valid-type]
        """Platform adapter for the Shadownet v0.2 protocol.

        Configuration comes from environment variables (or a parsed
        ``shadow://connect`` URI), resolved during ``connect()``.
        """

        async def connect(self) -> bool:
            self._stack = AsyncExitStack()
            self._gateway = getattr(self._message_handler, "__self__", None)
            try:
                self._client = await self._stack.enter_async_context(
                    ShadownetMCPClient.connect(
                        endpoint=self._mcp_endpoint,
                        access_token=self._token,
                    )
                )
                identity = await self._client.identity()
                self._shadowname: str = identity.shadowname
                self._inbox_task = asyncio.create_task(
                    self._inbox_loop(),
                    name=f"shadownet-inbox-{self._shadowname}",
                )
                self._mark_connected()
                _log.info(
                    "Shadownet plugin connected as %s (mcp=%s)",
                    self._shadowname,
                    self._mcp_endpoint,
                )
            except Exception:
                await self._stack.aclose()
                raise
            else:
                return True

        async def disconnect(self) -> None:
            task = getattr(self, "_inbox_task", None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                    _log.debug("inbox task ended during disconnect: %s", exc)
            stack = getattr(self, "_stack", None)
            if stack is not None:
                await stack.aclose()
            self._mark_disconnected()
            _log.info("Shadownet plugin disconnected")

        async def send(self, chat_id: str, content: str, **kwargs: object) -> SendResult:
            """Send a free-form text reply to a contact over A2A.

            Suppresses only an exact-duplicate resend to the same contact within
            a short window (anti-echo for an accidental double-dispatch); a
            distinct message is never dropped. Bounding an autonomous loop is the
            exchange engine's job, not a blanket cooldown here. The result
            reflects the sidecar's accept/reject status rather than always
            reporting success.
            """
            _, _, send_result_cls = _resolve_hermes_types()
            dedup_window = int(os.environ.get("SHADOWNET_SEND_DEDUP_SECONDS", "5"))
            now = time.time()
            last_content, last_ts = self._last_send.get(chat_id, ("", 0.0))
            if content == last_content and now - last_ts < dedup_window:
                _log.debug(
                    "[Shadownet] send() suppressed exact-duplicate to %s within %ss",
                    chat_id,
                    dedup_window,
                )
                return send_result_cls(success=True)

            # If this contact has an active exchange, thread the reply onto its
            # contextId via respond(); otherwise start a fresh send().
            context_id = self._engine.active_context_for(chat_id)
            if context_id:
                replied = await self._client.respond(
                    RespondInput(contextId=context_id, body=BodySlot(text=content))
                )
                status, message_id, error = replied.status, replied.message_id, replied.error
            else:
                sent = await self._client.send(SendInput(to=chat_id, body=BodySlot(text=content)))
                status, message_id, error = sent.status, sent.message_id, sent.error
            self._last_send[chat_id] = (content, now)
            stale = [
                cid for cid, (_c, ts) in self._last_send.items() if now - ts > dedup_window * 4
            ]
            for cid in stale:
                del self._last_send[cid]
            # status is a required Literal["accepted","rejected"]; read it directly so a
            # sidecar rejection surfaces rather than being assumed successful.
            return send_result_cls(
                success=(status == "accepted"), message_id=message_id, error=error
            )

        async def send_typing(self, chat_id: str, metadata: dict[str, Any] | None = None) -> None:
            """Shadownet is async / fire-and-forget — no typing indicator."""

        async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
            return {"id": chat_id, "platform": "shadownet"}

        async def _inbox_loop(self) -> None:
            """Long-poll inbox_wait per RFC 0002 §4 and dispatch each event."""
            last_event_id: str | None = None
            while True:
                try:
                    result = await self._client.inbox_wait(
                        InboxWaitInput(
                            timeout_seconds=self._long_poll_timeout,
                            last_event_id=last_event_id,
                        )
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    _log.warning("inbox_wait failed: %s — retrying in 5s", exc)
                    await asyncio.sleep(5.0)
                    continue
                last_event_id = result.next_event_id or last_event_id
                for event in result.events:
                    try:
                        await self._on_event(event)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        _log.exception("event dispatch failed for %s", event)

        async def _on_event(self, event: dict[str, Any]) -> None:
            """Route inbound Shadownet events.

            An inbox.message from a known contact is handled autonomously by the
            full Hermes agent; from a stranger it is left in the sidecar for the
            human to triage (pull — the pending-inbox hook surfaces it).
            task.update carries no proactive push; it is observed on the next look.
            """
            event_name = event.get("event")
            data = event.get("data") or {}

            if event_name == "task.update":
                _log.debug(
                    "task.update for context %s (status=%s) — pull model, not pushed",
                    data.get("contextId") or "",
                    data.get("status") or "",
                )
                return

            if event_name != "inbox.message":
                _log.debug(
                    "ignoring %s event (only inbox.message + task.update handled)",
                    event_name,
                )
                return

            sender = data.get("from") or ""
            sender_name = data.get("fromName") or sender
            context_id = data.get("contextId") or ""
            message_id = data.get("messageId") or ""
            intent = data.get("intent") or ""

            inbox_item = await self._fetch_inbox_item(message_id)
            body = inbox_item.body if inbox_item is not None else None
            body_text = (body.text if body is not None else "") or ""
            body_data = (body.data if body is not None else None) or {}
            status = (inbox_item.status if inbox_item is not None else "") or ""

            if not (body_text or body_data):
                _log.debug("inbox.message from %s carried no content; nothing to do", sender)
                return

            # The engine decides: a known contact is handled autonomously by the
            # full Hermes agent (free-form or a coordination intent); a stranger,
            # or a tripped runaway backstop, is left for the human to triage via
            # the pending-inbox hook + inbox skill (pull — no proactive push).
            decision = self._engine.decide(
                status=status,
                contact=sender,
                context_id=context_id,
                message_id=message_id,
            )
            if decision.action == "skip":
                _log.debug("shadownet: dropping duplicate message %s from %s", message_id, sender)
                return
            if decision.action == "autonomous":
                await self._run_autonomous_turn(
                    sender=sender,
                    sender_name=sender_name,
                    context_id=context_id,
                    message_id=message_id,
                    body_text=body_text,
                    body_data=body_data,
                    intent=intent,
                    first_turn=decision.first_turn,
                )
                return

            _log.debug(
                "shadownet: leaving %s from %s for human triage (%s)",
                message_id,
                sender,
                decision.reason,
            )

        async def _fetch_inbox_item(self, message_id: str) -> Any | None:
            if not message_id:
                return None
            try:
                result = await self._client.inbox(InboxInput(includeReview=True, limit=50))
            except Exception as exc:  # noqa: BLE001
                _log.debug("inbox fetch for %s failed: %s", message_id, exc)
                return None
            for item in result.items:
                if item.message_id == message_id:
                    return item
            return None

        async def _contact_notes_for(self, sender: str) -> str:
            """Cached ``ContactProfile.notes`` for a contact (the human's per-contact guidance)."""
            if sender in self._contact_notes:
                return self._contact_notes[sender]
            notes = ""
            try:
                detail = await self._client.contact_detail(ContactDetailInput(name=sender))
                profile = detail.profile
                notes = (profile.notes if profile is not None and profile.notes else "") or ""
            except Exception as exc:  # noqa: BLE001
                _log.debug("contact_detail for %s failed: %s", sender, exc)
            self._contact_notes[sender] = notes
            return notes

        async def _run_autonomous_turn(
            self,
            *,
            sender: str,
            sender_name: str,
            context_id: str,
            message_id: str,
            body_text: str,
            body_data: dict[str, Any],
            intent: str,
            first_turn: bool,
        ) -> None:
            """Run the full Hermes agent silently on a known contact's message.

            The turn runs in the shadownet session bound to the sender. For a
            free-form message the agent's reply IS the move (delivered to the peer
            via send()); for a coordination intent the agent makes typed moves with
            mcp_shadownet_respond per the shadownet-coordinate skill. Either way the
            human is not in this session — the agent surfaces to them only by
            calling send_message when a decision is needed or the flow completes.
            """
            if intent in _COORDINATION_INTENTS:
                skill = "shadownet-coordinate"
                text = _coordination_context(
                    sender_name=sender_name,
                    intent=intent,
                    body_text=body_text,
                    body_data=body_data,
                    context_id=context_id,
                )
            else:
                skill = "shadownet-autonomous"
                notes = await self._contact_notes_for(sender) if first_turn else ""
                text = _build_autonomous_inject(
                    sender_name=sender_name,
                    body_text=body_text,
                    notes=notes,
                    first_turn=first_turn,
                )
            source = self.build_source(
                chat_id=sender,
                chat_type="dm",
                user_id=sender,
                user_name=sender_name or sender,
            )
            try:
                synth_event = message_event_cls(
                    text=text,
                    source=source,
                    raw_message={
                        "event_kind": "shadownet.autonomous",
                        "context_id": context_id,
                        "message_id": message_id,
                        "intent": intent,
                    },
                    auto_skill=skill,
                )
                await self.handle_message(synth_event)
                _log.info(
                    "shadownet: autonomous %s turn for %s (context=%s, first=%s)",
                    skill,
                    sender,
                    context_id,
                    first_turn,
                )
            except Exception:
                _log.exception("shadownet: autonomous turn failed for %s", sender)

        def __init__(self, config: Any, ctx: Any = None) -> None:
            from gateway.config import Platform

            super().__init__(config, Platform("shadownet"))
            # Plugin context (None outside a Hermes install / in tests). Held so
            # the exchange engine can reach host LLM/tool surfaces in a later phase.
            self._ctx = ctx
            mcp_endpoint, token, timeout = _resolve_config(config)
            self._mcp_endpoint = mcp_endpoint
            self._token = token
            self._long_poll_timeout = timeout
            # chat_id -> (last content sent, timestamp), for exact-duplicate anti-echo.
            self._last_send: dict[str, tuple[str, float]] = {}
            # Switchboard + circuit-breaker for autonomous exchanges (in-memory).
            self._engine = ExchangeEngine()
            # contact shadowname -> ContactProfile.notes (cached human guidance).
            self._contact_notes: dict[str, str] = {}

    return ShadownetAdapter


def _build_autonomous_inject(
    *, sender_name: str, body_text: str, notes: str, first_turn: bool
) -> str:
    """Context injected into an autonomous turn: the move framing + the peer's message.

    On the first turn it carries the full framing plus the human's per-contact
    notes; later turns are light because the skill (re-loaded each turn) and the
    persisted Hermes session already hold the framing and history.
    """
    if not first_turn:
        return f"[shadownet-autonomous · {sender_name}]\n{body_text}"
    notes_line = f"Your user's notes on {sender_name}: {notes}\n" if notes else ""
    return (
        f"[AUTONOMOUS SHADOWNET EXCHANGE with {sender_name}]\n"
        f"You are conversing directly with {sender_name}, a known contact, on your "
        f"user's behalf. Your reply goes straight to them and IS your move — do not "
        f"call mcp_shadownet_send or mcp_shadownet_respond yourself.\n"
        f"{notes_line}"
        f"Follow the shadownet-autonomous skill. Surface to your user via send_message "
        f"only if the exchange completes, a decision is needed, or something notable "
        f"happens.\n\n"
        f"{sender_name} says:\n{body_text}"
    )


def _coordination_context(
    *, sender_name: str, intent: str, body_text: str, body_data: dict[str, Any], context_id: str
) -> str:
    """Thin context for an autonomous coordination turn: the structured facts only.

    The protocol detail lives in the shadownet-coordinate skill (single source);
    here we hand the agent the intent, the contextId to thread on, and the plan
    data so it can make its typed move and surface a decision when needed.
    """
    intent_short = intent.rsplit(":", 1)[-1] if intent else "message"
    try:
        data_json = json.dumps(body_data, indent=2) if body_data else "{}"
    except (TypeError, ValueError):
        data_json = "{}"
    return (
        f"[AUTONOMOUS SHADOWNET COORDINATION with {sender_name}]\n"
        f"intent: {intent_short}\n"
        f"context_id: {context_id}\n"
        f"Coordinate with {sender_name}, a known contact, on your user's behalf. "
        f"Follow the shadownet-coordinate skill: make your typed move with "
        f"mcp_shadownet_respond, and surface to your user via send_message only "
        f"when their decision is needed or the plan is settled.\n"
        f"Message: {body_text}\n"
        f"Data:\n{data_json}"
    )


def _resolve_config(config: Any) -> tuple[str, str, int]:
    """Extract (mcp_endpoint, access_token, long_poll_timeout) from PlatformConfig + env.

    Hermes' adapter ``__init__`` receives a ``PlatformConfig`` whose
    ``extra`` dict carries platform-specific values, with environment
    variables as fallback. The v0.2 surface needs an MCP endpoint URL
    plus a bearer access token; both can come from ``SHADOWNET_CONNECT_URL``
    (the ``shadow://connect?mcp=…&token=…`` URI from RFC 0003) or
    individually via ``SHADOWNET_MCP_ENDPOINT`` + ``SHADOWNET_TOKEN``.
    """
    extras = getattr(config, "extra", None) or {}
    connect_uri = extras.get("connect_url") or os.environ.get("SHADOWNET_CONNECT_URL")
    mcp_endpoint: str | None = None
    token: str | None = None

    if connect_uri:
        parsed = parse_connect_uri(connect_uri)
        if not parsed.is_inline:
            raise RuntimeError(
                "SHADOWNET_CONNECT_URL must be an inline (token=...) form for "
                "the Hermes plugin; handoff URIs require a browser flow that "
                "the plugin does not run."
            )
        assert parsed.access_token is not None
        token = parsed.access_token
        mcp_endpoint = parsed.mcp_endpoint
    else:
        token = extras.get("token") or os.environ.get("SHADOWNET_TOKEN") or ""
        mcp_endpoint = (
            extras.get("mcp_endpoint")
            or os.environ.get("SHADOWNET_MCP_ENDPOINT")
            or DEFAULT_SIDECAR_BASE_URL
        )

    if not token:
        raise RuntimeError(
            "Shadownet plugin requires SHADOWNET_CONNECT_URL (or SHADOWNET_TOKEN); "
            "mint one from your Sidecar's onboarding portal."
        )
    if not mcp_endpoint:
        raise RuntimeError(
            "Shadownet plugin requires SHADOWNET_MCP_ENDPOINT (or a connect URI "
            "carrying the mcp= parameter)."
        )

    timeout_raw = (
        extras.get("long_poll_timeout_seconds")
        or os.environ.get("SHADOWNET_LONG_POLL_TIMEOUT_SECONDS")
        or "30"
    )
    try:
        timeout = int(timeout_raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"SHADOWNET_LONG_POLL_TIMEOUT_SECONDS must be an integer, got {timeout_raw!r}"
        ) from exc
    return mcp_endpoint, token, max(1, timeout)


def check_shadownet_requirements() -> bool:
    """Module-level requirements check invoked by Hermes during platform discovery.

    Returns True iff the runtime environment can support the adapter — for
    v0.2 Shadownet, that's "has a connect URI or a token+endpoint pair".
    Live MCP reachability is verified inside ``connect()``.
    """
    if os.environ.get("SHADOWNET_CONNECT_URL"):
        return True
    return bool(os.environ.get("SHADOWNET_TOKEN") and os.environ.get("SHADOWNET_MCP_ENDPOINT"))


def env_enablement() -> dict[str, Any] | None:
    """Surface the plugin in ``hermes gateway status`` from env alone."""
    if (
        os.environ.get("SHADOWNET_CONNECT_URL")
        or os.environ.get("SHADOWNET_TOKEN")
        or os.environ.get("SHADOWNET_MCP_ENDPOINT")
    ):
        return {}
    return None
