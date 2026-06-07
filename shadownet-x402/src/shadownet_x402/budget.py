"""Per-identity spend budget and an operator deny-list (the kill switch)."""

from __future__ import annotations

from typing import Protocol

from shadownet_x402.errors import BudgetError


class BudgetStore(Protocol):
    def allowed(self, identity_key: str) -> bool: ...

    def remaining(self, identity_key: str) -> int: ...

    def reserve(self, identity_key: str, amount: int) -> None: ...

    def record(self, identity_key: str, amount: int) -> None: ...


class InMemoryBudgetStore:
    """In-process per-identity spend cap with a revocation deny-list."""

    def __init__(self, *, cap_micro: int) -> None:
        self._cap = cap_micro
        self._spent: dict[str, int] = {}
        self._revoked: set[str] = set()

    def allowed(self, identity_key: str) -> bool:
        return identity_key not in self._revoked

    def remaining(self, identity_key: str) -> int:
        return max(0, self._cap - self._spent.get(identity_key, 0))

    def reserve(self, identity_key: str, amount: int) -> None:
        if not self.allowed(identity_key):
            raise BudgetError(f"identity {identity_key!r} is revoked")
        if self._spent.get(identity_key, 0) + amount > self._cap:
            raise BudgetError("payment would exceed the per-identity budget")

    def record(self, identity_key: str, amount: int) -> None:
        self._spent[identity_key] = self._spent.get(identity_key, 0) + amount

    def revoke(self, identity_key: str) -> None:
        self._revoked.add(identity_key)

    def restore(self, identity_key: str) -> None:
        self._revoked.discard(identity_key)
