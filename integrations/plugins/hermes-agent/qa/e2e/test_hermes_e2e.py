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
    assert any(c["name"] == "identity" and c["transport"] == "mcp" for c in calls), (
        f"adapter never called identity over MCP; trace={calls}"
    )
    assert any(c["name"] == "inbox_wait" and c["transport"] == "mcp" for c in calls), (
        f"adapter never long-polled inbox_wait; trace={calls}"
    )


def test_inbound_event_delivered_during_held_long_poll() -> None:
    # The adapter is parked in a held inbox_wait long-poll (the mock holds the
    # connection open for the full timeout). Enqueue an event; the in-flight poll
    # must return it promptly, the adapter must consume it and advance its cursor
    # — proving the session stays open and delivers mid-poll, not on a fast loop.
    _reset()
    t0 = time.time()
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

    calls = _wait(advanced, timeout=20)
    assert advanced(calls), f"adapter did not consume/advance after the event; trace={calls}"
    elapsed = time.time() - t0
    assert elapsed < 10, f"event not delivered promptly during the held long-poll ({elapsed:.1f}s)"


def test_polling_loop_is_persistent() -> None:
    # The held long-poll returns ~every timeout; confirm the adapter keeps
    # re-polling on the same session (≥2 inbox_wait calls), not a one-shot.
    _reset()
    calls = _wait(
        lambda cs: sum(1 for c in cs if c["name"] == "inbox_wait" and c["transport"] == "mcp") >= 2,
        timeout=20,
    )
    n = sum(1 for c in calls if c["name"] == "inbox_wait" and c["transport"] == "mcp")
    assert n >= 2, (
        f"adapter did not keep polling on a persistent session (saw {n} inbox_wait); trace={calls}"
    )


def test_inbound_message_surfaces_to_agent() -> None:
    # Regression for the real cloud bug: a plain inbound message (no intent, no
    # SHADOWNET_NOTIFY_CHAT) must SURFACE as a Shadownet chat and reach a Hermes
    # turn — the agent then runs against the stub model and its reply flows back
    # out to the sender's DM (adapter.send(to=sender)) / the message context
    # (respond(contextId)). A SUPPRESSED message never reaches a turn, so no
    # reply is ever issued. We attribute the reply to THIS message (to == sender
    # / contextId == ours) so it can't be confused with any other test's traffic.
    httpx.post(f"{MOCK}/_reset", timeout=10)

    sender = "perfect@sh4dow.org"
    context_id = "ctx-surface-1"
    _enqueue(
        {
            "event": "inbox.message",
            "event_id": "e-surface-1",
            "data": {"from": sender, "messageId": "m-surface-1", "contextId": context_id},
            "body": "hi from perfect via shadownet",
        }
    )

    def replied_to_us(c: dict) -> bool:
        args = c.get("arguments") or {}
        return (c["name"] == "send" and args.get("to") == sender) or (
            c["name"] == "respond" and args.get("contextId") == context_id
        )

    calls = _wait(lambda cs: any(replied_to_us(c) for c in cs), timeout=120)
    names = [c["name"] for c in calls]
    assert any(replied_to_us(c) for c in calls), (
        "inbound message did not surface into an agent turn (no reply back to the "
        f"sender/context); trace={names}"
    )
