from __future__ import annotations

import pytest

from shadownet_x402.errors import ReplayError
from shadownet_x402.nonce import InMemoryNonceStore


def test_single_use() -> None:
    store = InMemoryNonceStore()
    nonce = store.issue(ttl=300)
    store.consume(nonce)
    with pytest.raises(ReplayError):
        store.consume(nonce)


def test_unknown() -> None:
    store = InMemoryNonceStore()
    with pytest.raises(ReplayError):
        store.consume("nope")


def test_expired() -> None:
    now = [0.0]
    store = InMemoryNonceStore(clock=lambda: now[0])
    nonce = store.issue(ttl=10)
    now[0] = 100.0
    with pytest.raises(ReplayError):
        store.consume(nonce)


def test_identity_binding() -> None:
    store = InMemoryNonceStore()
    nonce = store.issue(ttl=300, identity_key="alice")
    with pytest.raises(ReplayError):
        store.consume(nonce, identity_key="bob")
    store2 = InMemoryNonceStore()
    bound = store2.issue(ttl=300, identity_key="alice")
    store2.consume(bound, identity_key="alice")
