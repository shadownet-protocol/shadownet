from __future__ import annotations

import heapq
import itertools
import uuid
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = [
    "Delivery",
    "InMemoryWebhookQueue",
    "QueuedDelivery",
    "WebhookQueue",
]


@dataclass(frozen=True, slots=True)
class Delivery:
    """Outbound webhook delivery payload (immutable)."""

    url: str
    secret: str
    body: bytes
    sidecar_id: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class QueuedDelivery:
    """A :class:`Delivery` plus its scheduling state inside a queue."""

    id: str
    delivery: Delivery
    next_attempt_at: float
    attempt: int = 1


@runtime_checkable
class WebhookQueue(Protocol):
    """Storage + scheduling protocol for outbound webhooks.

    Default implementation is :class:`InMemoryWebhookQueue`. Consumers needing
    durability across restarts implement this protocol against their own
    persistent store (SQLite, Redis, …) and pass it to :class:`WebhookDispatcher`.

    All methods are synchronous — implementations that wrap async storage
    SHOULD wrap their I/O in ``asyncio.to_thread`` or use an in-memory cache
    plus background flush; the dispatcher loop calls these methods frequently.
    """

    def enqueue(self, delivery: Delivery, *, ready_at: float) -> str: ...

    def next_due(self, now: float) -> QueuedDelivery | None: ...

    def reschedule(self, delivery_id: str, *, ready_at: float, attempt: int) -> None: ...

    def ack(self, delivery_id: str) -> None: ...

    def mark_degraded(self, url: str) -> None: ...

    def clear_degraded(self, url: str) -> None: ...

    def is_degraded(self, url: str) -> bool: ...


class InMemoryWebhookQueue:
    """Default in-memory queue. Lossy across restarts; trivial to wrap."""

    def __init__(self) -> None:
        self._heap: list[tuple[float, int, str]] = []
        self._items: dict[str, QueuedDelivery] = {}
        self._counter = itertools.count()
        self._degraded: set[str] = set()

    def enqueue(self, delivery: Delivery, *, ready_at: float) -> str:
        delivery_id = uuid.uuid4().hex
        item = QueuedDelivery(id=delivery_id, delivery=delivery, next_attempt_at=ready_at)
        self._items[delivery_id] = item
        heapq.heappush(self._heap, (ready_at, next(self._counter), delivery_id))
        return delivery_id

    def next_due(self, now: float) -> QueuedDelivery | None:
        while self._heap:
            ready_at, _, delivery_id = self._heap[0]
            if delivery_id not in self._items:
                heapq.heappop(self._heap)  # ack'd or rescheduled stale entry
                continue
            item = self._items[delivery_id]
            if item.next_attempt_at != ready_at:
                heapq.heappop(self._heap)  # stale heap entry from a reschedule
                continue
            if ready_at > now:
                return None
            heapq.heappop(self._heap)
            return item
        return None

    def reschedule(self, delivery_id: str, *, ready_at: float, attempt: int) -> None:
        item = self._items.get(delivery_id)
        if item is None:
            return
        item.next_attempt_at = ready_at
        item.attempt = attempt
        heapq.heappush(self._heap, (ready_at, next(self._counter), delivery_id))

    def ack(self, delivery_id: str) -> None:
        self._items.pop(delivery_id, None)

    def mark_degraded(self, url: str) -> None:
        self._degraded.add(url)

    def clear_degraded(self, url: str) -> None:
        self._degraded.discard(url)

    def is_degraded(self, url: str) -> bool:
        return url in self._degraded
