"""Tier-1 integration assertions against a real Hermes gateway.

Proves what unit tests cannot: the published Hermes image loads our pip-
installed plugin (entry-point discovery + plugins.enabled), runs register(),
and the platform adapter opens a real MCP session to the shared mock Sidecar
and long-polls inbox_wait. Then an enqueued inbound event is consumed and the
adapter advances its poll cursor. Asserted entirely from the mock's trace.
"""

from __future__ import annotations

import os
import time

import httpx

MOCK = os.environ["MOCK_URL"]


def _calls() -> list[dict]:
    return httpx.get(f"{MOCK}/_calls", timeout=10).json()


def _reset() -> None:
    httpx.post(f"{MOCK}/_reset", timeout=10)


def _enqueue(event: dict) -> None:
    httpx.post(f"{MOCK}/_enqueue-inbox-event", json=event, timeout=10)


def _wait(pred, timeout: float = 120.0, interval: float = 0.5) -> list[dict]:
    deadline = time.time() + timeout
    calls: list[dict] = []
    while time.time() < deadline:
        calls = _calls()
        if pred(calls):
            return calls
        time.sleep(interval)
    return calls


def test_adapter_loads_and_connects() -> None:
    # Gateway boot + s6 init + plugin load + adapter MCP connect can take a while.
    def ready(cs: list[dict]) -> bool:
        return any(c["name"] == "identity" and c["transport"] == "mcp" for c in cs) and any(
            c["name"] == "inbox_wait" and c["transport"] == "mcp" for c in cs
        )

    calls = _wait(ready, timeout=180)
    assert any(
        c["name"] == "identity" and c["transport"] == "mcp" for c in calls
    ), f"adapter never called identity over MCP; trace={calls}"
    assert any(
        c["name"] == "inbox_wait" and c["transport"] == "mcp" for c in calls
    ), f"adapter never long-polled inbox_wait; trace={calls}"


def test_inbound_event_is_consumed() -> None:
    _reset()
    _enqueue(
        {
            "event": "inbox.message",
            "event_id": "e-driver-1",
            "data": {"from": "bob@sh4dow.org", "messageId": "m1", "contextId": "c1"},
        }
    )

    def advanced(cs: list[dict]) -> bool:
        return any(
            c["name"] == "inbox_wait" and c["arguments"].get("last_event_id") == "e-driver-1"
            for c in cs
        )

    calls = _wait(advanced, timeout=60)
    assert advanced(calls), f"adapter did not advance its poll cursor after the event; trace={calls}"