from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from shadownet.logging import get_logger
from shadownet.webhook.queue import Delivery, InMemoryWebhookQueue, WebhookQueue
from shadownet.webhook.verify import build_webhook_headers, ensure_url_allowed

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

# RFC-0007 §Retries.
#   Attempt 1: immediate
#   Attempt 2: +5s
#   Attempt 3: +30s
#   Attempt 4: +5min
#   Attempt 5: +30min
# After attempt 5 the URL is marked degraded.

RETRY_SCHEDULE_SECONDS: tuple[int, ...] = (0, 5, 30, 300, 1800)
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10

_log = get_logger(__name__)

__all__ = [
    "DEFAULT_REQUEST_TIMEOUT_SECONDS",
    "RETRY_SCHEDULE_SECONDS",
    "Delivery",
    "WebhookDispatcher",
]


class WebhookDispatcher:
    """Async dispatcher that drives a :class:`WebhookQueue` per RFC-0007.

    The caller owns the lifecycle: construct, ``await dispatcher.run()`` in a
    task, ``await dispatcher.dispatch(...)`` to enqueue. ``stop()`` cleanly
    cancels the worker.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        queue: WebhookQueue | None = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        retry_schedule: tuple[int, ...] = RETRY_SCHEDULE_SECONDS,
    ) -> None:
        self._http = http
        self._queue: WebhookQueue = queue if queue is not None else InMemoryWebhookQueue()
        self._timeout = request_timeout
        self._clock = clock
        self._schedule = retry_schedule
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()

    async def dispatch(
        self,
        *,
        url: str,
        secret: str,
        body: bytes,
        sidecar_id: str,
    ) -> str:
        """Schedule an immediate delivery attempt. Returns the queue id."""
        ensure_url_allowed(url)
        delivery = Delivery(url=url, secret=secret, body=body, sidecar_id=sidecar_id)
        delivery_id = self._queue.enqueue(delivery, ready_at=self._clock())
        self._wake.set()
        return delivery_id

    def liveness_signal(self, url: str) -> None:
        """Treat any successful host-agent interaction as liveness — clear degraded state."""
        if self._queue.is_degraded(url):
            _log.info("clearing degraded state for %s", url)
            self._queue.clear_degraded(url)

    @property
    def queue(self) -> WebhookQueue:
        return self._queue

    async def run(self) -> None:
        """Drain the queue until :meth:`stop` is called."""
        while not self._stop.is_set():
            now = self._clock()
            item = self._queue.next_due(now)
            if item is None:
                await self._wait_for_work(max_wait_seconds=1.0)
                continue
            await self._attempt(item)

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    async def _wait_for_work(self, *, max_wait_seconds: float) -> None:
        try:
            async with asyncio.timeout(max_wait_seconds):
                await self._wake.wait()
        except TimeoutError:
            return
        finally:
            self._wake.clear()

    async def _attempt(self, item) -> None:  # type: ignore[no-untyped-def]
        delivery = item.delivery
        headers = {
            **delivery.headers,
            **build_webhook_headers(
                delivery.body,
                secret=delivery.secret,
                sidecar_id=delivery.sidecar_id,
            ),
            "Content-Type": "application/json",
        }
        try:
            response = await self._http.post(
                delivery.url,
                content=delivery.body,
                headers=headers,
                timeout=self._timeout,
            )
        except Exception as exc:
            _log.warning("webhook %s attempt %d errored: %s", delivery.url, item.attempt, exc)
            self._record_failure(item)
            return
        if 200 <= response.status_code < 300:
            self._queue.ack(item.id)
            self._queue.clear_degraded(delivery.url)
            _log.debug(
                "webhook %s delivered on attempt %d (HTTP %d)",
                delivery.url,
                item.attempt,
                response.status_code,
            )
            return
        _log.warning(
            "webhook %s attempt %d returned HTTP %d",
            delivery.url,
            item.attempt,
            response.status_code,
        )
        self._record_failure(item)

    def _record_failure(self, item) -> None:  # type: ignore[no-untyped-def]
        next_attempt = item.attempt + 1
        if next_attempt > len(self._schedule):
            self._queue.mark_degraded(item.delivery.url)
            self._queue.ack(item.id)
            _log.warning("webhook %s exhausted retries; marked degraded", item.delivery.url)
            return
        delay = self._schedule[next_attempt - 1]
        self._queue.reschedule(
            item.id,
            ready_at=self._clock() + delay,
            attempt=next_attempt,
        )
