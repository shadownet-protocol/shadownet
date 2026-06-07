from __future__ import annotations

import pytest

from shadownet_x402.budget import InMemoryBudgetStore
from shadownet_x402.errors import BudgetError


def test_under_cap() -> None:
    budget = InMemoryBudgetStore(cap_micro=1000)
    budget.reserve("alice", 600)
    budget.record("alice", 600)
    assert budget.remaining("alice") == 400


def test_over_cap() -> None:
    budget = InMemoryBudgetStore(cap_micro=1000)
    budget.record("alice", 900)
    with pytest.raises(BudgetError):
        budget.reserve("alice", 200)


def test_revoke_and_restore() -> None:
    budget = InMemoryBudgetStore(cap_micro=1000)
    budget.revoke("alice")
    assert not budget.allowed("alice")
    with pytest.raises(BudgetError):
        budget.reserve("alice", 1)
    budget.restore("alice")
    budget.reserve("alice", 1)
