"""Single-use payment nonces for x402 replay defense."""

from __future__ import annotations

import secrets
import time
from typing import TYPE_CHECKING, Protocol

from shadownet_x402.errors import ReplayError

if TYPE_CHECKING:
    from collections.abc import Callable


class NonceStore(Protocol):
    def issue(self, *, ttl: int, identity_key: str | None = None) -> str: ...

    def consume(self, nonce: str, *, identity_key: str | None = None) -> None: ...


class InMemoryNonceStore:
    """In-process single-use nonce store for one resource server."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._entries: dict[str, tuple[str | None, float]] = {}

    def issue(self, *, ttl: int, identity_key: str | None = None) -> str:
        nonce = secrets.token_urlsafe(16)
        self._entries[nonce] = (identity_key, self._clock() + ttl)
        return nonce

    def consume(self, nonce: str, *, identity_key: str | None = None) -> None:
        entry = self._entries.pop(nonce, None)
        if entry is None:
            raise ReplayError("unknown or already-used nonce")
        bound_identity, expiry = entry
        if expiry < self._clock():
            raise ReplayError("nonce expired")
        if bound_identity is not None and bound_identity != identity_key:
            raise ReplayError("nonce bound to a different identity")
