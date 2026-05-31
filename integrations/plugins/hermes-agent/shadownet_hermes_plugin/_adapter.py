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

The v0.1 ``data_type`` event taxonomy (``coordination_request``,
``response``, ``confirmation``, ``confirmed``) is gone — v0.2 dispatch is
driven by RFC 0002 §5 intent URIs (``urn:shadownet:intent:coordinate_v1``,
``…:confirm_plan_v1``, ``…:accept_plan_v1``). v0.1's ``intentId``
correlation handle is replaced by A2A's ``contextId`` (RFC 0001 §8.2).
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
    InboxInput,
    InboxWaitInput,
    SendInput,
    ShadownetMCPClient,
)
from shadownet.mcp.intents import (
    ACCEPT_PLAN_V1_URI,
    CONFIRM_PLAN_V1_URI,
    COORDINATE_V1_URI,
)
from shadownet.mcp.tools import BodySlot
from shadownet.onboarding import parse_connect_uri

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
            """Send a free-form text response to a contact, with loop prevention.

            ``chat_id`` is the recipient Shadowname (or bare public key in
            direct mode). Allows one send per contact per cooldown window;
            subsequent sends are suppressed to prevent A2A feedback loops.
            """
            send_cooldown = int(os.environ.get("SHADOWNET_SEND_COOLDOWN_SECONDS", "120"))
            now = time.time()
            last = self._send_timestamps.get(chat_id, 0.0)
            if now - last < send_cooldown:
                _log.debug(
                    "[Shadownet] send() suppressed (cooldown): chat_id=%s elapsed=%.1fs",
                    chat_id,
                    now - last,
                )
                _, _, send_result_cls = _resolve_hermes_types()
                return send_result_cls(success=True)

            self._send_timestamps[chat_id] = now
            self._evict_stale_timestamps(self._send_timestamps, send_cooldown * 2)
            await self._client.send(SendInput(to=chat_id, body=BodySlot(text=content)))
            _, _, send_result_cls = _resolve_hermes_types()
            return send_result_cls(success=True)

        async def send_typing(self, chat_id: str) -> None:
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

            Event taxonomy per RFC 0002 §7:

            - ``inbox.message`` with a typed ``intent`` → branch on the
              intent URI: ``coordinate_v1`` dispatches into a synthetic
              ``shadownet`` session (the receiver's autonomous-negotiation
              skill); ``confirm_plan_v1`` and ``accept_plan_v1`` inject
              into the user-facing chat session via
              ``SHADOWNET_NOTIFY_CHAT``.
            - ``inbox.message`` without an intent (or unrecognized) → if
              ``status == inbox`` and a notify target is configured, inject
              as a free-form message.
            - ``task.update`` → inject the task-state transition.
            - Other event types → suppress.
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
            context_id = data.get("contextId") or ""
            message_id = data.get("messageId") or ""
            intent = data.get("intent") or ""

            # The body isn't carried on the inbox.message event payload per
            # RFC 0002 §7 — fetch it via the inbox tool when we need it.
            inbox_item = await self._fetch_inbox_item(message_id)
            body = inbox_item.body if inbox_item is not None else None
            body_text = (body.text if body is not None else "") or ""
            body_data = (body.data if body is not None else None) or {}

            if intent == COORDINATE_V1_URI:
                await self._dispatch_coordinate(
                    sender=sender,
                    context_id=context_id,
                    message_id=message_id,
                    body_text=body_text,
                    body_data=body_data,
                    event_id=event_id,
                )
                return

            if intent in (CONFIRM_PLAN_V1_URI, ACCEPT_PLAN_V1_URI):
                await self._inject_plan_event(
                    sender=sender,
                    context_id=context_id,
                    message_id=message_id,
                    intent=intent,
                    body_text=body_text,
                    body_data=body_data,
                    notify_target=notify_target,
                )
                return

            # Free-form inbound (no intent, or one we don't model). If the
            # operator pointed us at a user-facing session, inject as a
            # plain message so the user sees it.
            if notify_target and body_text:
                await self._inject_free_form(
                    sender=sender,
                    context_id=context_id,
                    message_id=message_id,
                    body_text=body_text,
                    notify_target=notify_target,
                )
            else:
                _log.debug(
                    "Suppressed free-form inbox.message from %s (no notify target)",
                    sender,
                )

        async def _fetch_inbox_item(self, message_id: str) -> Any | None:
            if not message_id:
                return None
            try:
                result = await self._client.inbox(InboxInput(limit=50))
            except Exception as exc:  # noqa: BLE001
                _log.debug("inbox fetch for %s failed: %s", message_id, exc)
                return None
            for item in result.items:
                if item.message_id == message_id:
                    return item
            return None

        async def _dispatch_coordinate(
            self,
            *,
            sender: str,
            context_id: str,
            message_id: str,
            body_text: str,
            body_data: dict[str, Any],
            event_id: str,
        ) -> None:
            """Open a synthetic shadownet session for the receiver-branch agent."""
            text = _build_receiver_prompt(
                sender=sender,
                context_id=context_id,
                message_id=message_id,
                body_text=body_text,
                body_data=body_data,
            )
            source = self.build_source(
                chat_id=sender,
                chat_type="dm",
                user_id=sender,
                user_name=sender,
            )
            try:
                msg_event = message_event_cls(
                    text=text,
                    source=source,
                    raw_message={"event_id": event_id, "intent": COORDINATE_V1_URI},
                    # On a new session Hermes auto-loads this skill so the
                    # agent has the receiver-branch protocol context.
                    auto_skill="shadownet-coordinate",
                )
                await self.handle_message(msg_event)
            except Exception:
                _log.exception("failed to dispatch coordinate_v1 %s", event_id)

        async def _inject_plan_event(
            self,
            *,
            sender: str,
            context_id: str,
            message_id: str,
            intent: str,
            body_text: str,
            body_data: dict[str, Any],
            notify_target: str,
        ) -> None:
            """Inject a confirm_plan_v1 or accept_plan_v1 event into the user session."""
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

            inject_text = _build_initiator_inject(
                sender=sender,
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

        def __init__(self, config: Any) -> None:
            from gateway.config import Platform

            super().__init__(config, Platform("shadownet"))
            mcp_endpoint, token, timeout = _resolve_config(config)
            self._mcp_endpoint = mcp_endpoint
            self._token = token
            self._long_poll_timeout = timeout
            self._send_timestamps: dict[str, float] = {}
            self._notify_timestamps: dict[str, float] = {}

        @staticmethod
        def _evict_stale_timestamps(timestamps: dict[str, float], max_age: float) -> None:
            """Remove entries older than max_age to prevent unbounded growth."""
            now = time.time()
            stale = [k for k, v in timestamps.items() if now - v > max_age]
            for k in stale:
                del timestamps[k]

    return ShadownetAdapter


def _build_receiver_prompt(
    *,
    sender: str,
    context_id: str,
    message_id: str,
    body_text: str,
    body_data: dict[str, Any],
) -> str:
    """Build the prompt the receiver-branch agent sees for a coordinate_v1 inbound.

    Emits ``context_id`` and ``message_id`` as separate plain-text lines so
    Hermes' built-in ``session_search`` tool can recall the thread on
    later turns even after the live context window has rolled.
    """
    activity = body_data.get("activity") if isinstance(body_data, dict) else None
    details = body_data.get("details") if isinstance(body_data, dict) else None
    activity_line = f"activity: {activity}\n" if activity else ""
    details_line = f"details: {details}\n" if details else ""
    return (
        f"[SHADOWNET COORDINATION REQUEST from {sender}]\n"
        f"context_id: {context_id}\n"
        f"message_id: {message_id}\n"
        f"{activity_line}"
        f"{details_line}"
        f"Request body: {body_text}\n\n"
        f"INSTRUCTIONS: You are the RECEIVER in a Shadownet coordination.\n"
        f"1. Read the request above.\n"
        f"2. Decide on a concrete plan autonomously (pick a specific time, "
        f"place, and any relevant details based on your user's preferences).\n"
        f"3. Call mcp_shadownet_respond with body containing intent="
        f"urn:shadownet:intent:confirm_plan_v1 and a typed PlanObject in data:\n"
        f'   contextId="{context_id}"\n'
        f'   body={{"text":"<one-line summary>","intent":"'
        f'{CONFIRM_PLAN_V1_URI}","data":<PlanObject>}}\n'
        f"4. Say NOTHING to the user. Your only output is the tool call.\n"
        f"5. End immediately after the tool call."
    )


def _build_initiator_inject(
    *,
    sender: str,
    context_id: str,
    message_id: str,
    intent: str,
    body_text: str,
    body_data: dict[str, Any],
) -> str:
    """Build the text injected into the user's chat session for plan events."""
    if intent == CONFIRM_PLAN_V1_URI:
        plan_summary = _summarize_plan(body_data, fallback=body_text)
        return (
            f"[SHADOWNET PLAN PROPOSED by {sender}]\n"
            f"context_id: {context_id}\n"
            f"message_id: {message_id}\n"
            f"Plan: {plan_summary}\n\n"
            f"INSTRUCTIONS: Present this plan to the user concisely and ask them "
            f"to confirm. When they say yes/confirm/ok, call "
            f"mcp_shadownet_confirm_plan with:\n"
            f'  name="{sender}"\n'
            f'  contextId="{context_id}"\n'
            f"  plan=<the PlanObject above>\n"
            f"If they decline, just say you'll let {sender} know."
        )

    if intent == ACCEPT_PLAN_V1_URI:
        return (
            f"[SHADOWNET PLAN ACCEPTED by {sender}]\n"
            f"context_id: {context_id}\n"
            f"message_id: {message_id}\n"
            f"Both sides have accepted; the plan is fully confirmed.\n\n"
            f"INSTRUCTIONS: Tell your user the plan is confirmed — {sender} accepted. "
            f"Be brief and celebratory. No tool calls needed."
        )

    return f"[SHADOWNET {intent} from {sender}]\ncontext_id: {context_id}\n{body_text}"


def _summarize_plan(body_data: dict[str, Any], *, fallback: str) -> str:
    if not isinstance(body_data, dict):
        return fallback[:300] if fallback else "<no details>"
    activity = body_data.get("activity") or ""
    when = body_data.get("when") or ""
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
