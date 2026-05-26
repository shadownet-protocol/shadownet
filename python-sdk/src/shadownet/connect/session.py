from __future__ import annotations

import asyncio
import random
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from pydantic import BaseModel, ConfigDict, Field

from shadownet.connect.errors import MCPSessionError
from shadownet.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# RFC-0007 amendment D — long-poll inbound via `social_inbox_wait` MCP tool.
#
# Plugins on hosts whose MCP SDK can't dispatch unknown notifications
# (notably the Python SDK, which validates against a closed
# `ServerNotification` union and drops unknowns at the receive loop) use
# this path. Plugins on hosts whose MCP SDK supports custom notifications
# (notably the TypeScript SDK via `setNotificationHandler`) can subscribe
# to `notifications/shadownet/*` instead. Sidecar emits both; cursors are
# shared so cross-transport bridges can dedupe.

DEFAULT_INBOX_TIMEOUT_SECONDS = 30
DEFAULT_RECONNECT_BASE_SECONDS = 1.0
DEFAULT_RECONNECT_MAX_SECONDS = 30.0
INBOX_WAIT_TOOL = "social_inbox_wait"

__all__ = [
    "DEFAULT_INBOX_TIMEOUT_SECONDS",
    "DEFAULT_RECONNECT_BASE_SECONDS",
    "DEFAULT_RECONNECT_MAX_SECONDS",
    "INBOX_WAIT_TOOL",
    "InboxEvent",
    "InboxWaitResult",
    "MCPSessionError",
    "ShadownetMCPSession",
]

_log = get_logger(__name__)


class InboxEvent(BaseModel):
    """A single event delivered via `social_inbox_wait`.

    Field shape matches the event payload defined in RFC-0007 § Events.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True, frozen=True)

    event_id: str = Field(min_length=1)
    event: str = Field(min_length=1)
    occurred_at: int = Field(ge=0, alias="occurredAt")
    data: dict[str, Any]


class InboxWaitResult(BaseModel):
    """Return shape of `social_inbox_wait`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True, frozen=True)

    events: list[InboxEvent] = Field(default_factory=list)
    next_event_id: str | None = None


class ShadownetMCPSession:
    """Async-context wrapper around `mcp.ClientSession` for a Shadownet sidecar.

    Usage::

        async with ShadownetMCPSession(
            base_url="https://app.sh4dow.org",
            shadowname="alice@app.sh4dow.org",
            token="...",
        ) as session:
            result = await session.call_tool("social_send", {"to": "...", "body": "..."})
            await session.inbox_loop(handler=on_event)

    Lifecycle: opening the context manager establishes the streamable-HTTP
    transport and initializes the MCP session. Closing tears both down.
    The wrapper does **not** run an inbox loop on its own — call
    :meth:`inbox_loop` from a task you own (typically a Hermes adapter's
    `connect()` or a Claude Code monitor's main).

    Reconnection is the caller's responsibility for the session itself
    (open a new one), but :meth:`inbox_loop` handles transient
    disconnection internally with exponential backoff so callers don't
    need to wrap it.
    """

    def __init__(
        self,
        *,
        base_url: str,
        shadowname: str,
        token: str,
        mcp_endpoint: str | None = None,
        client_name: str = "shadownet-python-sdk",
        client_version: str = "0.3.0",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._shadowname = shadowname
        self._token = token
        self._mcp_endpoint = mcp_endpoint
        self._client_name = client_name
        self._client_version = client_version
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    @property
    def mcp_url(self) -> str:
        """The per-tenant MCP endpoint (RFC-0007).

        Returns the explicit `mcp_endpoint` from the integration bundle when
        the caller supplied one; otherwise synthesizes
        `{base_url}/u/{shadowname}/mcp` (legacy behavior, fine for
        deployments where MCP shares the dashboard host).
        """
        if self._mcp_endpoint is not None:
            return self._mcp_endpoint
        # Shadowname format is `local@host`. URL-safe encoding of the local
        # part is the caller's responsibility — we don't second-guess.
        return f"{self._base_url}/u/{self._shadowname}/mcp"

    @property
    def session(self) -> ClientSession:
        """The underlying MCP `ClientSession`. Use for advanced cases."""
        if self._session is None:
            raise MCPSessionError("session is not open; use 'async with' first")
        return self._session

    async def __aenter__(self) -> ShadownetMCPSession:
        stack = AsyncExitStack()
        try:
            http = create_mcp_http_client(headers={"Authorization": f"Bearer {self._token}"})
            await stack.enter_async_context(http)
            streams = await stack.enter_async_context(
                streamable_http_client(self.mcp_url, http_client=http)
            )
            read_stream, write_stream, _get_session_id = streams
            from mcp import types as mcp_types

            session = await stack.enter_async_context(
                ClientSession(
                    read_stream,
                    write_stream,
                    client_info=mcp_types.Implementation(
                        name=self._client_name, version=self._client_version
                    ),
                )
            )
            await session.initialize()
        except Exception:
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session
        _log.debug("opened ShadownetMCPSession to %s", self.mcp_url)
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._session = None
        stack = self._stack
        self._stack = None
        if stack is not None:
            await stack.aclose()

    @classmethod
    def _wrap_session(
        cls,
        session: ClientSession,
        *,
        base_url: str = "https://test.invalid",
        shadowname: str = "test@test.invalid",
        token: str = "test-token",  # noqa: S107 — test fixture, not a real secret
    ) -> ShadownetMCPSession:
        """Test-only: wrap an existing :class:`ClientSession` (e.g. from the
        MCP SDK's in-memory transport) without going through the real HTTP
        setup. The instance is already "open" — do not use ``async with``
        on it; close the underlying session yourself.
        """
        instance = cls(base_url=base_url, shadowname=shadowname, token=token)
        instance._session = session
        return instance

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Proxy to the underlying ``ClientSession.call_tool``."""
        return await self.session.call_tool(name, arguments)

    async def wait_for_inbox(
        self,
        *,
        last_event_id: str | None = None,
        timeout_seconds: int = DEFAULT_INBOX_TIMEOUT_SECONDS,
    ) -> InboxWaitResult:
        """Single call to ``social_inbox_wait``.

        Use :meth:`inbox_loop` for the typical "background worker" pattern;
        this method is exposed for tests and for callers that want to
        manage their own loop.
        """
        result = await self.call_tool(
            INBOX_WAIT_TOOL,
            {
                "timeout_seconds": timeout_seconds,
                **({"last_event_id": last_event_id} if last_event_id is not None else {}),
            },
        )
        payload = _extract_structured(result)
        return InboxWaitResult.model_validate(payload)

    async def inbox_loop(
        self,
        handler: Callable[[InboxEvent], Awaitable[None]],
        *,
        timeout_seconds: int = DEFAULT_INBOX_TIMEOUT_SECONDS,
        starting_event_id: str | None = None,
        on_error: Callable[[Exception], Awaitable[Literal["retry", "stop"]]] | None = None,
        reconnect_base_seconds: float = DEFAULT_RECONNECT_BASE_SECONDS,
        reconnect_max_seconds: float = DEFAULT_RECONNECT_MAX_SECONDS,
    ) -> None:
        """Long-poll loop dispatching each inbox event to ``handler``.

        Runs forever. Cancel the surrounding task to stop. Catches
        transient transport errors and retries with exponential backoff
        between ``reconnect_base_seconds`` and ``reconnect_max_seconds``.
        Pass ``on_error`` to override that behavior — return ``"stop"``
        to exit the loop on the next failure, ``"retry"`` to keep going.

        The reconnect kwargs are exposed primarily so tests can pass
        very small values; production callers should leave the defaults.
        """
        cursor = starting_event_id
        backoff = reconnect_base_seconds
        while True:
            # Transport call is wrapped — its failures are retried. Handler
            # invocation is OUTSIDE the try so handler exceptions propagate
            # to the caller (a handler raising signals "stop the loop", not
            # "the connection blipped"). This separation is load-bearing:
            # a broad catch around the handler would mask user bugs and
            # cause CPU-bound retry spins.
            try:
                result = await self.wait_for_inbox(
                    last_event_id=cursor, timeout_seconds=timeout_seconds
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.warning("inbox_loop transient transport error: %s", exc)
                decision: Literal["retry", "stop"] = "retry"
                if on_error is not None:
                    decision = await on_error(exc)
                if decision == "stop":
                    raise
                jitter = random.uniform(0, backoff * 0.25)  # noqa: S311 — not crypto
                await asyncio.sleep(min(backoff + jitter, reconnect_max_seconds))
                backoff = min(backoff * 2, reconnect_max_seconds)
                continue

            backoff = reconnect_base_seconds  # reset on transport success
            for event in result.events:
                await handler(event)
                cursor = event.event_id
            if result.next_event_id is not None:
                cursor = result.next_event_id


def _extract_structured(call_tool_result: Any) -> dict[str, Any]:
    """Pull the structured payload out of an MCP `CallToolResult`.

    The MCP `call_tool` return shape carries content blocks plus an
    optional `structuredContent` field; the long-poll tool always sets
    `structuredContent`. Falls back to parsing the first text block if
    that field is absent (older sidecar implementations).
    """
    if getattr(call_tool_result, "isError", False):
        content = getattr(call_tool_result, "content", None) or []
        msg = " ".join(getattr(b, "text", "") for b in content).strip()
        raise MCPSessionError(f"{INBOX_WAIT_TOOL} tool error: {msg}")

    structured = getattr(call_tool_result, "structuredContent", None)
    if structured is not None:
        if isinstance(structured, dict):
            # MCP 1.27+ wraps string tool returns as {"result": "<json>"}
            if "result" in structured and isinstance(structured["result"], str):
                import json

                try:
                    parsed = json.loads(structured["result"])
                    if isinstance(parsed, dict):
                        return parsed
                except (ValueError, TypeError):
                    pass
            return structured
        if hasattr(structured, "model_dump"):
            return structured.model_dump(mode="json")  # type: ignore[no-any-return]
    content = getattr(call_tool_result, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            if not text.strip():
                raise MCPSessionError(
                    f"{INBOX_WAIT_TOOL} returned empty text content (possible transport "
                    f"corruption); isError={getattr(call_tool_result, 'isError', None)}"
                )
            import json

            try:
                return json.loads(text)  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                raise MCPSessionError(
                    f"{INBOX_WAIT_TOOL} returned unparseable text: {text[:200]!r}"
                ) from None
    raise MCPSessionError(
        f"{INBOX_WAIT_TOOL} returned no structured content or text block: {call_tool_result!r}"
    )


# Backward-compat: timedelta is sometimes natural; expose a constant at module
# load so callers don't need to construct one.
_DEFAULT_TIMEOUT = timedelta(seconds=DEFAULT_INBOX_TIMEOUT_SECONDS)
