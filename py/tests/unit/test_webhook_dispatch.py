from __future__ import annotations

import json

import httpx
import pytest

from shadownet.webhook.dispatch import RETRY_SCHEDULE_SECONDS, WebhookDispatcher
from shadownet.webhook.errors import WebhookURLInvalid
from shadownet.webhook.queue import InMemoryWebhookQueue


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _body(message: str = "hi") -> bytes:
    return json.dumps(
        {
            "shadownet:v": "0.1",
            "event": "inbox.message",
            "occurredAt": 0,
            "data": {"intentId": message},
        }
    ).encode()


async def _drain_due(dispatcher: WebhookDispatcher) -> None:
    item = dispatcher.queue.next_due(dispatcher._clock())  # type: ignore[attr-defined]
    while item is not None:
        await dispatcher._attempt(item)  # type: ignore[attr-defined]
        item = dispatcher.queue.next_due(dispatcher._clock())  # type: ignore[attr-defined]


async def test_dispatch_delivers_on_first_try() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    clock = _FakeClock()
    async with httpx.AsyncClient(transport=transport) as http:
        dispatcher = WebhookDispatcher(http, clock=clock)
        await dispatcher.dispatch(
            url="https://example.com/webhook",
            secret="topsecret",
            body=_body(),
            sidecar_id="sc-01",
        )
        await _drain_due(dispatcher)
    assert len(captured) == 1
    sent = captured[0]
    assert sent.headers["X-Shadownet-Sidecar-Id"] == "sc-01"
    assert sent.headers["X-Shadownet-Sidecar-Sig"].startswith("sha256=")
    assert sent.headers["Content-Type"] == "application/json"


async def test_dispatch_retries_on_5xx_and_marks_degraded() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(len(attempts) + 1)
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    clock = _FakeClock()
    async with httpx.AsyncClient(transport=transport) as http:
        dispatcher = WebhookDispatcher(http, clock=clock)
        await dispatcher.dispatch(
            url="https://example.com/webhook",
            secret="topsecret",
            body=_body(),
            sidecar_id="sc-01",
        )
        # Walk through every retry slot in the spec schedule.
        for delay in RETRY_SCHEDULE_SECONDS:
            clock.t += delay
            await _drain_due(dispatcher)
    assert len(attempts) == len(RETRY_SCHEDULE_SECONDS)
    assert dispatcher.queue.is_degraded("https://example.com/webhook")


async def test_liveness_signal_clears_degraded() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(500))
    clock = _FakeClock()
    async with httpx.AsyncClient(transport=transport) as http:
        dispatcher = WebhookDispatcher(http, clock=clock)
        dispatcher.queue.mark_degraded("https://example.com/webhook")
        dispatcher.liveness_signal("https://example.com/webhook")
    assert not dispatcher.queue.is_degraded("https://example.com/webhook")


async def test_dispatch_rejects_disallowed_url() -> None:
    async with httpx.AsyncClient() as http:
        dispatcher = WebhookDispatcher(http)
        with pytest.raises(WebhookURLInvalid):
            await dispatcher.dispatch(
                url="http://attacker.example/inbox",
                secret="x",
                body=_body(),
                sidecar_id="sc-01",
            )


async def test_in_memory_queue_basic_roundtrip() -> None:
    q = InMemoryWebhookQueue()
    from shadownet.webhook.queue import Delivery

    d = Delivery(url="https://example.com/x", secret="s", body=b"{}", sidecar_id="sc-01")
    qid = q.enqueue(d, ready_at=0.0)
    item = q.next_due(0.0)
    assert item is not None
    assert item.id == qid
    q.ack(qid)
    assert q.next_due(0.0) is None


async def test_in_memory_queue_reschedule_skips_stale_heap_entry() -> None:
    q = InMemoryWebhookQueue()
    from shadownet.webhook.queue import Delivery

    d = Delivery(url="https://example.com/x", secret="s", body=b"{}", sidecar_id="sc-01")
    qid = q.enqueue(d, ready_at=0.0)
    q.reschedule(qid, ready_at=10.0, attempt=2)
    # Now=5: not yet due; the stale heap entry at ready_at=0 must be skipped.
    assert q.next_due(5.0) is None
    item = q.next_due(20.0)
    assert item is not None
    assert item.attempt == 2
