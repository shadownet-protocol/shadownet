from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Literal

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import BaseModel, ConfigDict, Field

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
    behavior: Literal["events", "empty", "raise"] = "events",
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
        if behavior == "raise":
            raise RuntimeError("simulated transport blip")
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


async def test_wait_for_inbox_round_trip() -> None:
    server = _build_server()
    async with _wrapped_session(server) as wrapper:
        result = await wrapper.wait_for_inbox(timeout_seconds=5)
    assert isinstance(result, InboxWaitResult)
    assert len(result.events) == 2
    assert result.events[0].event_id == "evt-1"
    assert result.events[0].data == {"from": "alice@x", "body": "hi"}
    assert result.next_event_id == "evt-3"
    assert server._test_state["last_args"]["timeout_seconds"] == 5  # type: ignore[attr-defined]


async def test_wait_for_inbox_passes_cursor() -> None:
    server = _build_server()
    async with _wrapped_session(server) as wrapper:
        await wrapper.wait_for_inbox(last_event_id="evt-prior", timeout_seconds=5)
    assert server._test_state["last_args"]["last_event_id"] == "evt-prior"  # type: ignore[attr-defined]


async def test_inbox_loop_dispatches_and_advances_cursor() -> None:
    events_sequence = [
        [
            {"event_id": "evt-1", "event": "inbox.message", "occurredAt": 1, "data": {}},
            {"event_id": "evt-2", "event": "inbox.message", "occurredAt": 2, "data": {}},
        ],
        [
            {"event_id": "evt-3", "event": "inbox.message", "occurredAt": 3, "data": {}},
        ],
    ]
    server = _build_server(events_per_call=events_sequence)
    received: list[InboxEvent] = []

    async def handler(event: InboxEvent) -> None:
        received.append(event)
        if len(received) == 3:
            raise _StopLoop

    async with _wrapped_session(server) as wrapper:
        with pytest.raises(_StopLoop):
            await wrapper.inbox_loop(handler, timeout_seconds=1)

    assert [e.event_id for e in received] == ["evt-1", "evt-2", "evt-3"]
    # Second call should have used evt-2 (last delivered) as cursor
    assert server._test_state["last_args"]["last_event_id"] == "evt-2"  # type: ignore[attr-defined]


async def test_inbox_loop_retries_with_backoff_on_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing call triggers backoff; on_error overrides default retry."""
    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr("shadownet.connect.session.asyncio.sleep", fake_sleep)

    server = _build_server(behavior="raise")
    received: list[InboxEvent] = []
    call_count = {"n": 0}

    async def handler(event: InboxEvent) -> None:
        received.append(event)

    async def on_error(exc: Exception) -> Literal["retry", "stop"]:
        call_count["n"] += 1
        if call_count["n"] >= 3:
            return "stop"
        return "retry"

    async with _wrapped_session(server) as wrapper:
        with pytest.raises(Exception, match="simulated transport blip"):
            await wrapper.inbox_loop(handler, timeout_seconds=1, on_error=on_error)

    assert received == []
    assert call_count["n"] == 3
    # Backoff doubles each transient failure (with jitter ≤ 25%).
    assert len(sleeps) == 2  # only sleeps between retries, not before stop
    assert sleeps[0] >= 1.0
    assert sleeps[1] >= 2.0


async def test_inbox_loop_propagates_cancellation() -> None:
    server = _build_server(behavior="empty")

    async def handler(event: InboxEvent) -> None:
        pass

    async with _wrapped_session(server) as wrapper:
        task = asyncio.create_task(wrapper.inbox_loop(handler, timeout_seconds=1))
        await asyncio.sleep(0.05)  # let the loop enter wait_for_inbox once
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_session_property_raises_before_open() -> None:
    wrapper = ShadownetMCPSession(base_url="https://x", shadowname="alice@x", token="t")
    with pytest.raises(MCPSessionError, match="not open"):
        _ = wrapper.session


def test_mcp_url_is_rfc_0007_path() -> None:
    wrapper = ShadownetMCPSession(
        base_url="https://app.example/", shadowname="alice@app.example", token="t"
    )
    assert wrapper.mcp_url == "https://app.example/u/alice@app.example/mcp"


class _StopLoop(Exception):
    """Sentinel raised by test handlers to terminate inbox_loop."""
