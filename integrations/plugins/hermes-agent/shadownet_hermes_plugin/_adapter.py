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

Coordination intents (coordinate_v1, propose_plan_v1, confirm_plan_v1,
accept_plan_v1) are application-level flows transported via the sidecar's
generic send/respond/inbox tools. The sidecar is content-agnostic; all
intent interpretation and flow orchestration lives here in the plugin.
All coordination events are injected into the user's chat session
(human-in-the-loop) — the agent never acts autonomously on coordination.
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

# Intent URI for free-form `respond` calls when the inbound payload doesn't
# fit one of the typed v0.2 intents.
INTENT_FREE_FORM_RESPONSE = "urn:shadownet:intent:response_v1"


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
            return send_result_cls(success=(status == "accepted"), message_id=message_id, error=error)

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

            All coordination intents are injected into the user's chat
            session (human-in-the-loop). The sidecar is content-agnostic;
            intent interpretation happens entirely here.
            """
            notify_target = os.environ.get("SHADOWNET_NOTIFY_CHAT", "")
            event_name = event.get("event")
            data = event.get("data") or {}
            event_id = event.get("eventId") or event.get("event_id") or ""

            if event_name == "task.update":
                await self._inject_task_update(data, event_id, notify_target)
                return

            if event_name != "inbox.message":
                _log.debug(
                    "ignoring %s event (only inbox.message + task.update dispatched)",
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

            coordination_intents = {
                COORDINATE_V1_URI,
                PROPOSE_PLAN_V1_URI,
                CONFIRM_PLAN_V1_URI,
                ACCEPT_PLAN_V1_URI,
            }

            if intent in coordination_intents:
                await self._inject_coordination_event(
                    sender=sender,
                    sender_name=sender_name,
                    context_id=context_id,
                    message_id=message_id,
                    intent=intent,
                    body_text=body_text,
                    body_data=body_data,
                    notify_target=notify_target,
                )
                return

            if not body_text:
                _log.debug(
                    "inbox.message from %s carried no body text; nothing to surface",
                    sender,
                )
                return

            # Free-form message: the engine decides whether the full Hermes agent
            # handles it silently (a known contact) or we surface it to the human
            # (a stranger pending review, or the runaway backstop tripped).
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
                    first_turn=decision.first_turn,
                )
                return

            _log.debug(
                "shadownet: surfacing %s from %s to human (%s)",
                message_id,
                sender,
                decision.reason,
            )
            if notify_target:
                await self._inject_free_form(
                    sender=sender_name,
                    context_id=context_id,
                    message_id=message_id,
                    body_text=body_text,
                    notify_target=notify_target,
                )
            else:
                await self._surface_inbound_message(
                    sender=sender,
                    sender_name=sender_name,
                    context_id=context_id,
                    message_id=message_id,
                    body_text=body_text,
                    event_id=event_id,
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
            first_turn: bool,
        ) -> None:
            """Run the full Hermes agent silently on a known contact's message.

            The turn runs in the shadownet session bound to the sender; the
            agent's reply IS the move and is delivered to the peer via send()
            (threaded by contextId). The human is not in this session — the
            agent surfaces to them only by calling send_message when warranted.
            """
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
                    },
                    auto_skill="shadownet-autonomous",
                )
                await self.handle_message(synth_event)
                _log.info(
                    "shadownet: autonomous turn for %s (context=%s, first=%s)",
                    sender,
                    context_id,
                    first_turn,
                )
            except Exception:
                _log.exception("shadownet: autonomous turn failed for %s", sender)

        async def _inject_coordination_event(
            self,
            *,
            sender: str,
            sender_name: str,
            context_id: str,
            message_id: str,
            intent: str,
            body_text: str,
            body_data: dict[str, Any],
            notify_target: str,
        ) -> None:
            """Inject any coordination intent into the user's chat session."""
            if not notify_target:
                _log.warning(
                    "Got %s from %s but SHADOWNET_NOTIFY_CHAT not set — cannot notify user",
                    intent,
                    sender,
                )
                return

            now = time.time()
            dedup_key = f"{sender}:{intent}:{context_id}"
            last = self._notify_timestamps.get(dedup_key, 0.0)
            notify_cooldown = int(os.environ.get("SHADOWNET_NOTIFY_COOLDOWN_SECONDS", "60"))
            if now - last < notify_cooldown:
                _log.debug("Dedup suppressed inject: %s (%.1fs ago)", dedup_key, now - last)
                return
            self._notify_timestamps[dedup_key] = now
            self._evict_stale_timestamps(self._notify_timestamps, notify_cooldown * 2)

            target = self._resolve_notify_target(notify_target)
            if target is None:
                return
            target_platform, chat_id, adapter = target

            inject_text = _build_coordination_inject(
                sender=sender,
                sender_name=sender_name,
                context_id=context_id,
                message_id=message_id,
                intent=intent,
                body_text=body_text,
                body_data=body_data,
            )

            from gateway.session import SessionSource

            source = SessionSource(
                platform=target_platform,
                chat_id=chat_id,
                user_id=chat_id,
                user_name="shadownet",
            )
            try:
                synth_event = message_event_cls(
                    text=inject_text,
                    source=source,
                    internal=True,
                    raw_message={
                        "intent": intent,
                        "context_id": context_id,
                        "message_id": message_id,
                    },
                    auto_skill="shadownet-coordinate",
                )
                await adapter.handle_message(synth_event)
                _log.info(
                    "Injected %s from %s (context=%s) into %s:%s session",
                    intent,
                    sender,
                    context_id,
                    target_platform.value,
                    chat_id,
                )
            except Exception:
                _log.exception(
                    "Failed to inject into %s:%s session", target_platform.value, chat_id
                )

        async def _inject_free_form(
            self,
            *,
            sender: str,
            context_id: str,
            message_id: str,
            body_text: str,
            notify_target: str,
        ) -> None:
            target = self._resolve_notify_target(notify_target)
            if target is None:
                return
            target_platform, chat_id, adapter = target

            inject_text = (
                f"[SHADOWNET MESSAGE from {sender}]\n"
                f"context_id: {context_id}\n"
                f"message_id: {message_id}\n"
                f"{body_text}\n\n"
                f"INSTRUCTIONS: Surface this message to the user. If they want "
                f"to reply, use mcp_shadownet_respond with the context_id above."
            )

            from gateway.session import SessionSource

            source = SessionSource(
                platform=target_platform,
                chat_id=chat_id,
                user_id=chat_id,
                user_name="shadownet",
            )
            try:
                synth_event = message_event_cls(
                    text=inject_text,
                    source=source,
                    internal=True,
                    raw_message={
                        "context_id": context_id,
                        "message_id": message_id,
                    },
                    auto_skill="shadownet-inbox",
                )
                await adapter.handle_message(synth_event)
            except Exception:
                _log.exception(
                    "Failed to inject free-form into %s:%s session",
                    target_platform.value,
                    chat_id,
                )

        async def _surface_inbound_message(
            self,
            *,
            sender: str,
            sender_name: str,
            context_id: str,
            message_id: str,
            body_text: str,
            event_id: str,
        ) -> None:
            """Surface a plain inbound A2A message as a Shadownet chat.

            With no SHADOWNET_NOTIFY_CHAT bridge, route the message through the
            platform-adapter pipeline (``handle_message``) so Hermes opens a
            session bound to the sender — the channel the plugin advertises — and
            auto-loads the ``shadownet-inbox`` skill so the agent has the
            context-id and the respond instructions. Without this a plain inbound
            message is silently dropped and never reaches the user.
            """
            source = self.build_source(
                chat_id=sender,
                chat_type="dm",
                user_id=sender,
                user_name=sender_name or sender,
            )
            text = (
                f"[SHADOWNET MESSAGE from {sender}]\n"
                f"context_id: {context_id}\n"
                f"message_id: {message_id}\n\n"
                f"{body_text}"
            )
            try:
                synth_event = message_event_cls(
                    text=text,
                    source=source,
                    raw_message={
                        "event_id": event_id,
                        "context_id": context_id,
                        "message_id": message_id,
                    },
                    auto_skill="shadownet-inbox",
                )
                await self.handle_message(synth_event)
                _log.info(
                    "shadownet: surfaced inbox.message from %s (context=%s, message=%s) as a shadownet chat",
                    sender,
                    context_id,
                    message_id,
                )
            except Exception:
                _log.exception("failed to surface inbox.message from %s", sender)

        async def _inject_task_update(
            self, data: dict[str, Any], event_id: str, notify_target: str
        ) -> None:
            """Inject a ``task.update`` event into the user-facing session.

            Per RFC 0002 §7, task.update carries ``{contextId, taskId, status}``
            and is only emitted for application-opened A2A Task workflows
            (Shadownet's standard envelope responses use A2A Message and do
            NOT generate this event). Dedup is keyed on (contextId, status)
            so repeated polling doesn't spam.
            """
            context_id = data.get("contextId") or ""
            task_id = data.get("taskId") or ""
            status = data.get("status") or "unknown"

            if not context_id:
                _log.debug("task.update without contextId, ignoring: %s", event_id)
                return

            if not notify_target:
                _log.debug(
                    "task.update for context %s (status=%s) but SHADOWNET_NOTIFY_CHAT "
                    "not set — nothing to notify",
                    context_id,
                    status,
                )
                return

            now = time.time()
            dedup_key = f"task.update:{context_id}:{status}"
            last = self._notify_timestamps.get(dedup_key, 0.0)
            notify_cooldown = int(os.environ.get("SHADOWNET_NOTIFY_COOLDOWN_SECONDS", "60"))
            if now - last < notify_cooldown:
                _log.debug("Dedup suppressed task.update: %s (%.1fs ago)", dedup_key, now - last)
                return
            self._notify_timestamps[dedup_key] = now
            self._evict_stale_timestamps(self._notify_timestamps, notify_cooldown * 2)

            target = self._resolve_notify_target(notify_target)
            if target is None:
                return
            target_platform, chat_id, adapter = target

            inject_text = _build_task_update_inject(context_id, task_id, status)

            from gateway.session import SessionSource

            source = SessionSource(
                platform=target_platform,
                chat_id=chat_id,
                user_id=chat_id,
                user_name="shadownet",
            )
            try:
                synth_event = message_event_cls(
                    text=inject_text,
                    source=source,
                    internal=True,
                    raw_message={
                        "context_id": context_id,
                        "task_id": task_id,
                        "status": status,
                        "event_type": "task.update",
                    },
                )
                await adapter.handle_message(synth_event)
                _log.info(
                    "Injected task.update context=%s status=%s into %s:%s session",
                    context_id,
                    status,
                    target_platform.value,
                    chat_id,
                )
            except Exception:
                _log.exception(
                    "Failed to inject task.update into %s:%s session",
                    target_platform.value,
                    chat_id,
                )

        def _resolve_notify_target(self, notify_target: str) -> tuple[Any, str, Any] | None:
            """Resolve ``platform:chat_id`` env target into (Platform, chat_id, adapter)."""
            parts = notify_target.split(":", 1)
            if len(parts) != 2:
                _log.warning(
                    "SHADOWNET_NOTIFY_CHAT must be 'platform:chat_id', got %r", notify_target
                )
                return None
            platform_name, chat_id = parts

            gateway = self._gateway
            if gateway is None:
                _log.warning("No gateway runner available for session injection")
                return None

            from gateway.config import Platform

            target_platform = Platform(platform_name)
            adapter = gateway.adapters.get(target_platform)
            if adapter is None:
                _log.warning("No adapter for platform %s", platform_name)
                return None
            return target_platform, chat_id, adapter

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
            self._notify_timestamps: dict[str, float] = {}
            # Switchboard + circuit-breaker for autonomous exchanges (in-memory).
            self._engine = ExchangeEngine()
            # contact shadowname -> ContactProfile.notes (cached human guidance).
            self._contact_notes: dict[str, str] = {}

        @staticmethod
        def _evict_stale_timestamps(timestamps: dict[str, float], max_age: float) -> None:
            """Remove entries older than max_age to prevent unbounded growth."""
            now = time.time()
            stale = [k for k, v in timestamps.items() if now - v > max_age]
            for k in stale:
                del timestamps[k]

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


def _build_coordination_inject(
    *,
    sender: str,
    sender_name: str,
    context_id: str,
    message_id: str,
    intent: str,
    body_text: str,
    body_data: dict[str, Any],
) -> str:
    """Build inject text for any coordination intent.

    Each intent has unambiguous semantics — no flowRole needed:
      coordinate_v1   → "someone wants to coordinate, ask your user"
      propose_plan_v1 → "someone proposed a plan, ask your user to confirm"
      confirm_plan_v1 → "someone confirmed your proposal, send accept"
      accept_plan_v1  → "fully done, tell the user"
    """
    if intent == COORDINATE_V1_URI:
        activity = body_data.get("activity", "") if isinstance(body_data, dict) else ""
        details = body_data.get("details", "") if isinstance(body_data, dict) else ""
        details_line = f"\nDetails: {details}" if details else ""
        return (
            f"[SHADOWNET COORDINATION REQUEST from {sender_name}]\n"
            f"context_id: {context_id}\n"
            f"message_id: {message_id}\n"
            f"Activity: {activity}{details_line}\n"
            f"Message: {body_text}\n\n"
            f'INSTRUCTIONS: {sender_name} wants to coordinate "{activity}". '
            f"You MUST propose a concrete plan IMMEDIATELY and AUTONOMOUSLY — "
            f"do NOT ask your user for input.\n\n"
            f"1. Check your user's calendar/availability if you have access. "
            f"If not, assume they are free.\n"
            f"2. Determine your user's city from memory/profile.\n"
            f"3. Search the web for a real, specific venue that fits the "
            f"activity (e.g. a popular cafe for coffee, a well-reviewed "
            f'restaurant for dinner). Never use "TBD" or placeholders.\n'
            f'4. Interpret the timing from the details (e.g. "Sunday morning" '
            f"→ next Sunday 10:00 AM). Pick a concrete ISO datetime.\n\n"
            f"Then call mcp_shadownet_respond with:\n"
            f'  contextId="{context_id}"\n'
            f"  body={{\n"
            f'    "text": "<activity> at <venue> on <date/time>",\n'
            f'    "intent": "urn:shadownet:intent:propose_plan_v1",\n'
            f'    "data": {{\n'
            f'      "activity": "{activity}",\n'
            f'      "when": "<ISO datetime you chose>",\n'
            f'      "where": {{"name": "<real venue>", "city": "<city>"}},\n'
            f'      "participants": []\n'
            f"    }}\n"
            f"  }}\n\n"
            f"After calling respond, say only: "
            f'"Replying to {sender_name}\'s agent."\n'
            f"Do not reveal proposal details — this step is autonomous. "
            f"Do not call respond more than once."
        )

    if intent == PROPOSE_PLAN_V1_URI:
        plan_summary = _summarize_plan(body_data, fallback=body_text)
        plan_json = "{}"
        try:
            plan_json = json.dumps(body_data, indent=2)
        except (TypeError, ValueError):
            pass
        return (
            f"[SHADOWNET PLAN PROPOSED by {sender_name}]\n"
            f"context_id: {context_id}\n"
            f"message_id: {message_id}\n"
            f"Plan: {plan_summary}\n"
            f"PlanObject:\n{plan_json}\n\n"
            f"INSTRUCTIONS: {sender_name} proposed a plan. Present it to the "
            f"user in a friendly, natural way and ask them to confirm. "
            f'Use "{sender_name}" when referring to the contact. Never show '
            f"raw identifiers, ISO timestamps, or JSON to the user.\n"
            f"When they say yes/confirm/ok, call mcp_shadownet_send with:\n"
            f'  to="{sender_name}"\n'
            f'  contextId="{context_id}"\n'
            f"  body={{\n"
            f'    "text": "Confirmed.",\n'
            f'    "intent": "urn:shadownet:intent:confirm_plan_v1",\n'
            f'    "data": {plan_json}\n'
            f"  }}\n"
            f'After sending, tell the user: "Sent confirmation to '
            f'{sender_name} — waiting for them to accept." '
            f"Do NOT say the plan is confirmed or finalized yet.\n"
            f"If they decline, just say you'll let {sender_name} know."
        )

    if intent == CONFIRM_PLAN_V1_URI:
        plan_summary = _summarize_plan(body_data, fallback=body_text)
        activity = body_data.get("activity", "") if isinstance(body_data, dict) else ""
        return (
            f"[SHADOWNET PLAN from {sender_name}]\n"
            f"context_id: {context_id}\n"
            f"message_id: {message_id}\n"
            f"Plan: {plan_summary}\n\n"
            f"INSTRUCTIONS: {sender_name} wants to meet up with your user. "
            f"Your agent already proposed a plan and {sender_name} agreed. "
            f'Present this to your user as: "{sender_name} wants to '
            f"{activity or 'meet up'} with you — <plan details in natural "
            f'language>. Would you like to accept?"\n'
            f"Give the user full context — they have NOT seen this plan before. "
            f"Never show raw identifiers, ISO timestamps, or JSON.\n"
            f"Do NOT call respond yet — wait for the user to say yes.\n"
            f"When the user confirms, call mcp_shadownet_respond with:\n"
            f'  contextId="{context_id}"\n'
            f"  body={{\n"
            f'    "text": "Accepted.",\n'
            f'    "intent": "urn:shadownet:intent:accept_plan_v1",\n'
            f'    "data": {{"acceptsMessageId": "{message_id}"}}\n'
            f"  }}\n"
            f"If they decline, just say you'll let {sender_name} know."
        )

    if intent == ACCEPT_PLAN_V1_URI:
        return (
            f"[SHADOWNET PLAN ACCEPTED by {sender_name}]\n"
            f"context_id: {context_id}\n"
            f"message_id: {message_id}\n"
            f"The plan is fully confirmed by both sides.\n\n"
            f"INSTRUCTIONS: Tell your user the plan is confirmed — {sender_name} "
            f"accepted. Be brief and celebratory. Never show raw identifiers "
            f"or ISO timestamps. No tool calls needed."
        )

    return f"[SHADOWNET message from {sender_name}]\ncontext_id: {context_id}\n{body_text}"


def _format_when(raw: str) -> str:
    """Turn an ISO datetime into a human-friendly string."""
    if not raw:
        return ""
    try:
        from datetime import datetime as _dt

        dt = _dt.fromisoformat(raw)
        hour12 = dt.hour % 12 or 12
        return (
            f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day} "
            f"at {hour12}:{dt.minute:02d} {dt.strftime('%p')}"
        )
    except (ValueError, TypeError):
        return raw


def _summarize_plan(body_data: dict[str, Any], *, fallback: str) -> str:
    if not isinstance(body_data, dict):
        return fallback[:300] if fallback else "<no details>"
    activity = body_data.get("activity") or ""
    when_raw = body_data.get("when") or ""
    when = _format_when(when_raw) if when_raw else ""
    where = body_data.get("where") or {}
    where_label = ""
    if isinstance(where, dict):
        where_label = where.get("name") or where.get("address") or where.get("city") or ""
    parts = [str(activity)] if activity else []
    if where_label:
        parts.append(f"at {where_label}")
    if when:
        parts.append(f"on {when}")
    if not parts:
        try:
            return json.dumps(body_data, separators=(",", ":"))[:300]
        except TypeError:
            return fallback[:300] if fallback else "<no details>"
    return " ".join(parts)


def _build_task_update_inject(context_id: str, task_id: str, status: str) -> str:
    """Build the inject text for an RFC 0002 §7 ``task.update`` event."""
    return (
        f"[SHADOWNET TASK UPDATE]\n"
        f"context_id: {context_id}\n"
        f"task_id: {task_id}\n"
        f"status: {status}\n\n"
        f"INSTRUCTIONS: A task's state changed. If this is the first time the "
        f"user is hearing about this status (or it's a terminal state like "
        f"'completed', 'failed', or 'canceled'), tell them concisely. "
        f"Otherwise stay silent — no tool calls, no acknowledgement. "
        f"Reference the context_id above if the user asks for more detail."
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
