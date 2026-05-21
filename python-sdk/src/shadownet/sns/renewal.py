from __future__ import annotations

import time
from typing import TYPE_CHECKING

from shadownet.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from shadownet.sns.record import SNSRecord

# RFC-0005 §Lifecycle — clients SHOULD re-register before
# `issued_at + ttl - max(60, ttl // 10)` to avoid resolution gaps.

_MIN_GRACE_SECONDS = 60

__all__ = ["due_at", "is_due", "renew_due"]

_log = get_logger(__name__)


def _grace(ttl: int) -> int:
    return max(_MIN_GRACE_SECONDS, ttl // 10)


def due_at(record: SNSRecord) -> int:
    """Wall-clock second at which ``record`` should be re-registered."""
    return record.issued_at + record.ttl - _grace(record.ttl)


def is_due(record: SNSRecord, *, now: int | None = None) -> bool:
    """True if ``record`` has crossed its renewal threshold."""
    moment = now if now is not None else int(time.time())
    return moment >= due_at(record)


async def renew_due(
    records: Iterable[SNSRecord],
    *,
    register: Callable[[SNSRecord], Awaitable[None]],
    now: int | None = None,
) -> list[SNSRecord]:
    """Invoke ``register`` for each record past its renewal threshold.

    ``register`` is the caller's existing registration path (SDKs do not
    expose a write-side SNS API at v0.1). The caller controls cadence,
    concurrency, and persistence — this helper only decides *which*
    records to refresh. Returns the records that were renewed.
    """
    moment = now if now is not None else int(time.time())
    renewed: list[SNSRecord] = []
    for record in records:
        if moment < due_at(record):
            continue
        _log.debug(
            "renewing SNS record for %s (iat=%d ttl=%d now=%d)",
            record.shadowname,
            record.issued_at,
            record.ttl,
            moment,
        )
        await register(record)
        renewed.append(record)
    return renewed
