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

        async def send(self, chat_id: str, text: str, **kwargs: object) -> SendResult:
            """Send an outbound A2A message via ``social_send``.

            Hermes platform adapter contract: ``chat_id`` identifies the
            destination — for Shadownet, this is a Shadowname (e.g.
            ``alice@example.org``) or a contact id known to the
            sidecar. We pass it through; the sidecar resolves.
            """
            session = self._session
            await session.call_tool(
                "social_send",
                {
                    "contactId": chat_id,
                    "interaction": "urn:shadownet:int:messaging.v0",
                    "payload": {"body": text},
                },
            )
            _, _, send_result_cls = _resolve_hermes_types()
            return send_result_cls(success=True)  # type: ignore[call-arg]

        async def send_typing(self, chat_id: str) -> None:
            """Shadownet is async / fire-and-forget — no typing indicator."""

        async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
            return {"id": chat_id, "platform": "shadownet"}

        async def _on_event(self, event: Any) -> None:
            """Dispatch one ``social_inbox_wait`` event into the agent.

            Only ``inbox.message`` events drive a turn at v1; other event
            types are logged and dropped (parity with the OpenClaw channel
            plugin's v1 behavior).
            """
            if event.event != "inbox.message":
                _log.debug("ignoring %s event (v1 dispatches inbox.message only)", event.event)
                return
            data = event.data or {}
            sender = data.get("from") or data.get("contactId") or "unknown"
            body = data.get("body") or ""
            message_event = message_event_cls(  # type: ignore[call-arg]
                platform="shadownet",
                chat_id=sender,
                sender_id=sender,
                text=body,
                raw={"event_id": event.event_id, "data": data},
            )
            await self.handle_message(message_event)  # type: ignore[attr-defined]

        async def _fetch_bundle(self, http: httpx.AsyncClient) -> IntegrationBundle:
            return await fetch_integration_bundle(
                http,
                base_url=self._sidecar_base_url,
                token=self._token,
            )

        # --- config plumbing -------------------------------------------------

        def __init__(self, config: Any) -> None:
            super().__init__(config)
            token, base_url, timeout = _resolve_config(config)
            self._token = token
            self._sidecar_base_url = base_url
            self._long_poll_timeout = timeout

    return ShadownetAdapter


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
