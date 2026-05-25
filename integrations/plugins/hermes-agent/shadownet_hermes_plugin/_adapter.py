"""Hermes Agent platform adapter for Shadownet.

This module is loaded inside a running Hermes Agent process. It MUST NOT
import Hermes types at module-import time (so the package remains
importable for testing and tooling outside a Hermes install). Hermes
types are deferred to function bodies or guarded by ``TYPE_CHECKING``.

The plugin model follows the Telegram precedent in
``gateway/platforms/telegram.py``: a per-account adapter holds a long-lived
outbound connection (here, an MCP session against the Shadownet sidecar),
runs an inbox loop in an ``asyncio.Task``, and dispatches each inbound
event to ``self.handle_message(MessageEvent)`` (NOT ``ctx.inject_message``,
which only works in CLI mode per the Hermes plugin reference).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from shadownet.connect.bundle import IntegrationBundle, fetch_integration_bundle
from shadownet.connect.session import ShadownetMCPSession
from shadownet.connect.url import parse_connect_url

if TYPE_CHECKING:
    # Hermes Agent ships its plugin-side types under these modules. They
    # only resolve inside a Hermes install — we use TYPE_CHECKING so
    # static analysis works even when the package isn't present locally.
    import httpx
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

DEFAULT_BASE_URL = "https://app.sh4dow.org"


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
        """Platform adapter for the Shadownet protocol.

        Configuration comes from environment variables (or a parsed
        ``shadownet://connect`` URL), resolved during ``connect()``.
        """

        async def connect(self) -> bool:
            self._stack = AsyncExitStack()
            self._gateway = getattr(self._message_handler, "__self__", None)
            try:
                http_client = await self._stack.enter_async_context(_build_http_client())
                bundle = await self._fetch_bundle(http_client)
                self._bundle: IntegrationBundle = bundle
                self._session = await self._stack.enter_async_context(
                    ShadownetMCPSession(
                        base_url=self._sidecar_base_url,
                        shadowname=bundle.shadowname,
                        token=self._token,
                    )
                )
                self._inbox_task = asyncio.create_task(
                    self._session.inbox_loop(
                        self._on_event,
                        timeout_seconds=self._long_poll_timeout,
                    ),
                    name=f"shadownet-inbox-{bundle.shadowname}",
                )
                self._mark_connected()
                _log.info(
                    "Shadownet plugin connected as %s (transport=inbox-wait, base=%s)",
                    bundle.shadowname,
                    self._sidecar_base_url,
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
            """Send a response to a contact via A2A, with loop prevention.

            Allows one send per contact per cooldown window. Subsequent sends
            to the same contact are suppressed to prevent A2A feedback loops.
            """
            send_cooldown = int(os.environ.get("SHADOWNET_SEND_COOLDOWN_SECONDS", "120"))
            now = time.time()
            last = self._send_timestamps.get(chat_id, 0)
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
            session = self._session
            await session.call_tool(
                "social_send",
                {
                    "contactId": chat_id,
                    "interaction": "urn:shadownet:int:messaging.v0",
                    "payload": {"body": content},
                },
            )
            _, _, send_result_cls = _resolve_hermes_types()
            return send_result_cls(success=True)

        async def send_typing(self, chat_id: str) -> None:
            """Shadownet is async / fire-and-forget — no typing indicator."""

        async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
            return {"id": chat_id, "platform": "shadownet"}

        async def _on_event(self, event: Any) -> None:
            """Route inbound Shadownet messages by type.

            - coordination_request → always process autonomously via handle_message
              (agent negotiates and calls social_respond, no user involvement)
            - response/confirmation/confirmed → inject into user's primary chat
              session (agent presents plan, user confirms/accepts)
            - other (message, noise) → suppress
            """
            if event.event != "inbox.message":
                _log.debug("ignoring %s event (v1 dispatches inbox.message only)", event.event)
                return
            data = event.data or {}
            contact_id = data.get("contactId") or "unknown"
            sender_name = data.get("from") or contact_id
            body = data.get("body") or ""
            data_type = data.get("data_type") or "message"
            intent_id = data.get("intentId") or ""

            if not body:
                return

            import os

            notify_target = os.environ.get("SHADOWNET_NOTIFY_CHAT", "")

            if data_type == "coordination_request":
                text = _build_receiver_prompt(body, data_type, intent_id, sender_name)
                from gateway.config import Platform
                from gateway.session import SessionSource

                source = SessionSource(
                    platform=Platform("shadownet"),
                    chat_id=contact_id,
                    user_id=contact_id,
                    user_name=sender_name,
                )
                try:
                    msg_event = message_event_cls(
                        text=text,
                        source=source,
                        raw_message={"event_id": event.event_id, "data": data},
                    )
                    await self.handle_message(msg_event)
                except Exception:
                    _log.exception("failed to dispatch coordination_request %s", event.event_id)

            elif data_type in ("response", "confirmation", "confirmed"):
                if notify_target:
                    await self._inject_to_user_session(sender_name, body, data_type, notify_target)
                else:
                    _log.warning(
                        "Got %s from %s but SHADOWNET_NOTIFY_CHAT not set — cannot notify user",
                        data_type,
                        sender_name,
                    )

            else:
                _log.debug("Suppressed %s event from %s", data_type, sender_name)

        async def _inject_to_user_session(
            self, sender_name: str, body: str, data_type: str, notify_target: str
        ) -> None:
            """Inject plan-related messages into the user's primary chat session.

            This creates a synthetic internal message on the user's Telegram
            (or other platform) session so the agent has context for follow-up
            actions like social_confirm_plan() or social_accept_plan().

            Uses the gateway's adapter registry (a public Dict[Platform, Adapter])
            — the same interface that DeliveryRouter and channel_directory use.
            """
            if data_type not in ("response", "confirmation", "confirmed"):
                _log.debug("Suppressed non-actionable %s from %s", data_type, sender_name)
                return

            now = time.time()
            dedup_key = f"{sender_name}:{data_type}"
            last = self._notify_timestamps.get(dedup_key, 0)
            notify_cooldown = int(os.environ.get("SHADOWNET_NOTIFY_COOLDOWN_SECONDS", "60"))
            if now - last < notify_cooldown:
                _log.debug("Dedup suppressed inject: %s (%.1fs ago)", dedup_key, now - last)
                return
            self._notify_timestamps[dedup_key] = now
            self._evict_stale_timestamps(self._notify_timestamps, notify_cooldown * 2)

            parts = notify_target.split(":", 1)
            if len(parts) != 2:
                _log.warning(
                    "SHADOWNET_NOTIFY_CHAT must be 'platform:chat_id', got %r", notify_target
                )
                return
            platform_name, chat_id = parts

            gateway = self._gateway
            if gateway is None:
                _log.warning("No gateway runner available for session injection")
                return

            from gateway.config import Platform
            from gateway.session import SessionSource

            target_platform = Platform(platform_name)
            adapter = gateway.adapters.get(target_platform)
            if adapter is None:
                _log.warning("No adapter for platform %s", platform_name)
                return

            inject_text = _build_initiator_inject(sender_name, body, data_type)

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
                )
                await adapter.handle_message(synth_event)
                _log.info(
                    "Injected %s from %s into %s:%s session",
                    data_type,
                    sender_name,
                    platform_name,
                    chat_id,
                )
            except Exception:
                _log.exception("Failed to inject into %s:%s session", platform_name, chat_id)

        async def _fetch_bundle(self, http: httpx.AsyncClient) -> IntegrationBundle:
            return await fetch_integration_bundle(
                http,
                base_url=self._sidecar_base_url,
                token=self._token,
            )

        # --- config plumbing -------------------------------------------------

        def __init__(self, config: Any) -> None:
            from gateway.config import Platform

            super().__init__(config, Platform("shadownet"))
            token, base_url, timeout = _resolve_config(config)
            self._token = token
            self._sidecar_base_url = base_url
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


def _build_receiver_prompt(body: str, data_type: str, intent_id: str, sender_name: str) -> str:
    """Build a protocol-aware prompt for the receiving agent.

    When a coordination_request arrives, we wrap it with instructions so the
    agent knows to use social_respond instead of generating free-form text.
    """
    if data_type == "coordination_request":
        return (
            f"[SHADOWNET COORDINATION REQUEST from {sender_name}]\n"
            f"Intent ID: {intent_id}\n"
            f"Request: {body}\n\n"
            f"INSTRUCTIONS: You are the RECEIVER in a Shadownet coordination.\n"
            f"1. Read the request above (activity + details).\n"
            f"2. Decide on a concrete plan autonomously (pick a specific time, "
            f"place, and any relevant details based on your user's preferences).\n"
            f"3. Call the tool: mcp_shadownet_social_respond with:\n"
            f'   intentId="{intent_id}"\n'
            f'   payload=\'{{"type":"response","status":"agreed","plan":{{"activity":"...","date":"...","time":"...","location":"...","notes":"..."}}}}\'\n'
            f"4. Say NOTHING to the user. Your only output is the tool call.\n"
            f"5. End immediately after the tool call."
        )
    if data_type == "confirmation":
        return (
            f"[SHADOWNET PLAN CONFIRMED by {sender_name}]\n"
            f"{body}\n\n"
            f"The initiator's user has confirmed the plan. Notify YOUR user:\n"
            f"Present the plan details and ask them to accept.\n"
            f"When they say yes, call: mcp_shadownet_social_accept_plan()"
        )
    return body


def _build_initiator_inject(sender_name: str, body: str, data_type: str) -> str:
    """Build the text injected into the user's chat session for plan events."""
    import json as _json

    if data_type == "response":
        try:
            parsed = _json.loads(body)
            plan = parsed.get("plan") if isinstance(parsed, dict) else None
        except (ValueError, TypeError):
            plan = None

        if plan and isinstance(plan, dict):
            activity = plan.get("activity", "meetup")
            date = plan.get("date", "")
            time_str = plan.get("time", "")
            location = plan.get("location", "")
            notes = plan.get("notes", "")
            plan_summary = f"{activity}"
            if location:
                plan_summary += f" at {location}"
            if date:
                plan_summary += f", {date}"
            if time_str:
                plan_summary += f" {time_str}"
            if notes:
                plan_summary += f" ({notes})"
        else:
            plan_summary = body[:300]

        return (
            f"[SHADOWNET PLAN RECEIVED from {sender_name}]\n"
            f"Plan: {plan_summary}\n\n"
            f"INSTRUCTIONS: Present this plan to the user in a concise, friendly message. "
            f"Ask them to confirm. When they say yes/confirm/ok, call: "
            f"mcp_shadownet_social_confirm_plan()\n"
            f"Do NOT call any other Shadownet tools. Just present and wait."
        )

    if data_type == "confirmation":
        try:
            parsed = _json.loads(body)
            plan = parsed.get("plan") if isinstance(parsed, dict) else None
        except (ValueError, TypeError):
            plan = None

        if plan and isinstance(plan, dict):
            activity = plan.get("activity", "meetup")
            date = plan.get("date", "")
            time_str = plan.get("time", "")
            location = plan.get("location", "")
            plan_summary = f"{activity}"
            if location:
                plan_summary += f" at {location}"
            if date:
                plan_summary += f", {date}"
            if time_str:
                plan_summary += f" {time_str}"
        else:
            plan_summary = body[:300]

        return (
            f"[SHADOWNET: Invitation from {sender_name}]\n"
            f"Plan: {plan_summary}\n\n"
            f"INSTRUCTIONS: {sender_name} is inviting your user to this plan. "
            f"Present the details (what, where, when) and ask your user if they want to accept.\n"
            f"When they say yes/accept/ok, call: mcp_shadownet_social_accept_plan()\n"
            f"If they decline, just say you'll let {sender_name} know.\n"
            f"IMPORTANT: Do NOT use the clarify tool. Do NOT call any other Shadownet tools. "
            f"Just write ONE short message presenting the invitation and wait for their reply."
        )

    if data_type == "confirmed":
        return (
            f"[SHADOWNET: {sender_name} ACCEPTED the plan]\n"
            f"Both sides have accepted. The plan is fully confirmed.\n\n"
            f"INSTRUCTIONS: Tell your user the plan is confirmed — {sender_name} accepted. "
            f"Be brief and celebratory. No tool calls needed."
        )

    return body


def _resolve_config(config: Any) -> tuple[str, str, int]:
    """Extract our settings from a Hermes ``PlatformConfig`` or env fallback.

    Hermes adapter ``__init__`` receives a ``PlatformConfig`` whose
    ``extra`` dict carries platform-specific values, with environment
    variables as fallback (per ``gateway/platforms/ADDING_A_PLATFORM.md``).
    We accept either path.
    """
    import os

    extras = getattr(config, "extra", None) or {}
    connect_url = extras.get("connect_url") or os.environ.get("SHADOWNET_CONNECT_URL")
    if connect_url:
        parsed = parse_connect_url(connect_url)
        if not parsed.is_inline:
            raise RuntimeError(
                "SHADOWNET_CONNECT_URL must be an inline (token=...) form for "
                "Hermes plugin install; handoff URLs require a separate "
                "browser flow not yet implemented in this plugin."
            )
        assert parsed.token is not None
        token = parsed.token
        base_url = parsed.base_url
    else:
        token = extras.get("token") or os.environ.get("SHADOWNET_TOKEN") or ""
        base_url = (
            extras.get("base_url")
            or os.environ.get("SHADOWNET_SIDECAR_BASE_URL")
            or DEFAULT_BASE_URL
        )
    if not token:
        raise RuntimeError(
            "Shadownet plugin requires SHADOWNET_TOKEN (or SHADOWNET_CONNECT_URL); "
            "mint one at <SHADOWNET_SIDECAR_BASE_URL>/connect/hermes-agent."
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
    return token, base_url.rstrip("/"), max(1, timeout)


def _build_http_client() -> httpx.AsyncClient:
    import httpx

    return httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))


def check_shadownet_requirements() -> bool:
    """Module-level requirements check invoked by Hermes during platform discovery.

    Returns True iff the runtime environment can support the adapter — for
    Shadownet, that's "has a token or connect URL." Sidecar reachability
    is verified inside ``connect()``.
    """
    import os

    return bool(os.environ.get("SHADOWNET_TOKEN") or os.environ.get("SHADOWNET_CONNECT_URL"))
