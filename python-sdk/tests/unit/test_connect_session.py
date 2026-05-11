from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, Literal

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

from shadownet.connect.errors import MCPSessionError
from shadownet.connect.session import (
    INBOX_WAIT_TOOL,
    InboxEvent,
    InboxWaitResult,
    ShadownetMCPSession,
)


class _ToolOutput(BaseModel):
    """The shape FastMCP serializes into ``structuredContent`` for the tool."""

    model_config = ConfigDict(populate_by_name=True)

    events: list[dict[str, Any]] = Field(default_factory=list)
    next_event_id: str | None = None


def _build_server(
    *,
    behavior: Literal["events", "empty"] = "events",
    events_per_call: list[list[dict[str, Any]]] | None = None,
    next_id: str = "evt-3",
) -> FastMCP:
    """FastMCP server exposing a `social_inbox_wait` tool with scripted behavior."""
    server = FastMCP(name="shadownet-test")
    state: dict[str, Any] = {"call_count": 0}
    if events_per_call is None:
        events_per_call = [
            [
                {
                    "event_id": "evt-1",
                    "event": "inbox.message",
                    "occurredAt": 100,
                    "data": {"from": "alice@x", "body": "hi"},
                },
                {
                    "event_id": "evt-2",
                    "event": "inbox.message",
                    "occurredAt": 101,
                    "data": {"from": "bob@x", "body": "yo"},
                },
            ]
        ]

    @server.tool(name=INBOX_WAIT_TOOL, description="Long-poll inbox.")
    async def _wait(timeout_seconds: int = 30, last_event_id: str | None = None) -> _ToolOutput:
        state["call_count"] += 1
        state["last_args"] = {"timeout_seconds": timeout_seconds, "last_event_id": last_event_id}
        if behavior == "empty":
            return _ToolOutput(events=[], next_event_id=last_event_id)
        idx = state["call_count"] - 1
        events = events_per_call[idx] if idx < len(events_per_call) else []
        return _ToolOutput(events=events, next_event_id=next_id if events else last_event_id)

    server._test_state = state  # type: ignore[attr-defined]
    return server


@contextlib.asynccontextmanager
async def _wrapped_session(server: FastMCP):
    async with create_connected_server_and_client_session(server) as session:
        yield ShadownetMCPSession._wrap_session(session)


# A fake session is used for backoff/error/cancellation tests so we don't have
# to spin up FastMCP for behaviors that have nothing to do with the wire
# protocol. Duck-typed against the small slice of ClientSession we actually
# call (``call_tool``).


class _FakeSession:
    def __init__(self, behavior: Callable[[int], Awaitable[Any]]) -> None:
        self._behavior = behavior
        self.calls = 0

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls += 1
        return await self._behavior(self.calls)


def _wrap_fake(fake: _FakeSession) -> ShadownetMCPSession:
    return ShadownetMCPSession._wrap_session(fake)  # type: ignore[arg-type]


# Bound every test that runs the loop so a misbehaving wrapper can't hang CI.
LOOP_TEST_TIMEOUT = 5.0


async def test_wait_for_inbox_round_trip() -> None:
    server = _build_server()
    async with _wrapped_session(server) as wrapper:
        result = await asyncio.wait_for(
            wrapper.wait_for_inbox(timeout_seconds=5), timeout=LOOP_TEST_TIMEOUT
        )
    assert isinstance(result, InboxWaitResult)
    assert len(result.events) == 2
    assert result.events[0].event_id == "evt-1"
    assert result.events[0].data == {"from": "alice@x", "body": "hi"}
    assert result.next_event_id == "evt-3"
    assert server._test_state["last_args"]["timeout_seconds"] == 5  # type: ignore[attr-defined]


async def test_wait_for_inbox_passes_cursor() -> None:
    server = _build_server()
    async with _wrapped_session(server) as wrapper:
        await asyncio.wait_for(
            wrapper.wait_for_inbox(last_event_id="evt-prior", timeout_seconds=5),
            timeout=LOOP_TEST_TIMEOUT,
        )
    assert server._test_state["last_args"]["last_event_id"] == "evt-prior"  # type: ignore[attr-defined]


async def test_inbox_loop_dispatches_and_advances_cursor() -> None:
    """Use a fake session so the test is hermetic and fast."""

    payloads = [
        {
            "events": [
                {"event_id": "evt-1", "event": "inbox.message", "occurredAt": 1, "data": {}},
                {"event_id": "evt-2", "event": "inbox.message", "occurredAt": 2, "data": {}},
            ],
            "next_event_id": "evt-2",
        },
        {
            "events": [
                {"event_id": "evt-3", "event": "inbox.message", "occurredAt": 3, "data": {}},
            ],
            "next_event_id": "evt-3",
        },
    ]
    last_args: dict[str, Any] = {}

    async def behavior(call_n: int) -> _FakeCallResult:
        # Capture last arguments for the cursor assertion below.
        nonlocal_args = arguments_holder["args"]
        last_args.update(nonlocal_args)
        idx = call_n - 1
        if idx < len(payloads):
            return _fake_call_result(payloads[idx])
        return _fake_call_result({"events": [], "next_event_id": last_args.get("last_event_id")})

    arguments_holder: dict[str, dict[str, Any]] = {"args": {}}

    fake = _FakeSession(behavior)

    # Wrap call_tool so we capture args.
    orig = fake.call_tool

    async def capture(name: str, arguments: dict[str, Any]) -> Any:
        arguments_holder["args"] = arguments
        return await orig(name, arguments)

    fake.call_tool = capture  # type: ignore[method-assign]

    received: list[InboxEvent] = []

    async def handler(event: InboxEvent) -> None:
        received.append(event)
        if len(received) == 3:
            raise _StopLoop

    wrapper = _wrap_fake(fake)
    with pytest.raises(_StopLoop):
        await asyncio.wait_for(
            wrapper.inbox_loop(handler, timeout_seconds=1, reconnect_base_seconds=0.01),
            timeout=LOOP_TEST_TIMEOUT,
        )

    assert [e.event_id for e in received] == ["evt-1", "evt-2", "evt-3"]
    assert last_args["last_event_id"] == "evt-2"


async def test_inbox_loop_retries_with_backoff_then_stops() -> None:
    """on_error="stop" exits the loop on the next failure; default would retry forever."""

    async def always_raise(call_n: int) -> Any:
        raise RuntimeError("simulated transport blip")

    fake = _FakeSession(always_raise)
    on_error_calls = 0

    async def on_error(exc: Exception) -> Literal["retry", "stop"]:
        nonlocal on_error_calls
        on_error_calls += 1
        if on_error_calls >= 3:
            return "stop"
        return "retry"

    received: list[InboxEvent] = []

    async def handler(event: InboxEvent) -> None:
        received.append(event)

    wrapper = _wrap_fake(fake)
    with pytest.raises(RuntimeError, match="simulated transport blip"):
        await asyncio.wait_for(
            wrapper.inbox_loop(
                handler,
                timeout_seconds=1,
                on_error=on_error,
                reconnect_base_seconds=0.001,  # tiny so backoff doesn't slow the test
                reconnect_max_seconds=0.01,
            ),
            timeout=LOOP_TEST_TIMEOUT,
        )

    assert received == []
    assert on_error_calls == 3
    assert fake.calls == 3


async def test_inbox_loop_propagates_cancellation() -> None:
    """A cancel of the surrounding task must propagate cleanly out of inbox_loop."""

    started = asyncio.Event()

    async def slow(call_n: int) -> Any:
        started.set()
        # Sleep long enough for the test to cancel us.
        await asyncio.sleep(60)
        return _fake_call_result({"events": [], "next_event_id": None})

    fake = _FakeSession(slow)

    async def handler(event: InboxEvent) -> None:
        pass

    wrapper = _wrap_fake(fake)
    task = asyncio.create_task(
        wrapper.inbox_loop(handler, timeout_seconds=1, reconnect_base_seconds=0.001)
    )
    await asyncio.wait_for(started.wait(), timeout=LOOP_TEST_TIMEOUT)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=LOOP_TEST_TIMEOUT)


async def test_session_property_raises_before_open() -> None:
    wrapper = ShadownetMCPSession(base_url="https://x", shadowname="alice@x", token="t")
    with pytest.raises(MCPSessionError, match="not open"):
        _ = wrapper.session


def test_mcp_url_is_rfc_0007_path() -> None:
    wrapper = ShadownetMCPSession(
        base_url="https://app.example/", shadowname="alice@app.example", token="t"
    )
    assert wrapper.mcp_url == "https://app.example/u/alice@app.example/mcp"


# --- helpers ----------------------------------------------------------------


class _StopLoop(Exception):
    """Sentinel raised by test handlers to terminate inbox_loop."""


class _FakeCallResult:
    """Minimal duck-typed stand-in for mcp.types.CallToolResult."""

    def __init__(self, structured: dict[str, Any]) -> None:
        self.structuredContent = structured
        self.content: list[Any] = []


def _fake_call_result(structured: dict[str, Any]) -> _FakeCallResult:
    return _FakeCallResult(structured)
