from __future__ import annotations

import pytest

from shadownet_hermes_plugin._engine import ExchangeEngine


def test_stranger_review_goes_to_human() -> None:
    eng = ExchangeEngine()
    d = eng.decide(status="stranger_review", contact="x@h", context_id="c1", now=1.0)
    assert d.action == "human"
    assert d.reason == "stranger_review"


def test_unknown_status_goes_to_human() -> None:
    eng = ExchangeEngine()
    assert eng.decide(status="", contact="x@h", context_id="c1", now=1.0).action == "human"


def test_known_contact_first_message_is_autonomous_first_turn() -> None:
    eng = ExchangeEngine()
    d = eng.decide(status="inbox", contact="alice@h", context_id="c1", message_id="m1", now=1.0)
    assert d.action == "autonomous"
    assert d.first_turn is True
    assert eng.active_context_for("alice@h") == "c1"


def test_subsequent_message_advances_turn_not_first() -> None:
    eng = ExchangeEngine()
    eng.decide(status="inbox", contact="alice@h", context_id="c1", message_id="m1", now=1.0)
    d2 = eng.decide(status="inbox", contact="alice@h", context_id="c1", message_id="m2", now=2.0)
    assert d2.action == "autonomous"
    assert d2.first_turn is False
    assert eng.active() == [("alice@h", "c1", 2)]


def test_duplicate_message_id_is_skipped() -> None:
    eng = ExchangeEngine()
    eng.decide(status="inbox", contact="alice@h", context_id="c1", message_id="m1", now=1.0)
    d = eng.decide(status="inbox", contact="alice@h", context_id="c1", message_id="m1", now=2.0)
    assert d.action == "skip"
    assert d.reason == "duplicate"


def test_max_turns_guard_surfaces_to_human(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_MAX_AUTO_TURNS", "3")
    eng = ExchangeEngine()
    for i in range(3):
        d = eng.decide(
            status="inbox", contact="a@h", context_id="c1", message_id=f"m{i}", now=float(i)
        )
        assert d.action == "autonomous"
    capped = eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m99", now=99.0)
    assert capped.action == "human"
    assert capped.reason == "max_turns"


def test_idle_resets_turn_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_MAX_AUTO_TURNS", "2")
    monkeypatch.setenv("SHADOWNET_AUTO_IDLE_SECONDS", "100")
    eng = ExchangeEngine()
    eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m1", now=1.0)
    eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m2", now=2.0)
    # Budget exhausted at 2 turns -> next would be human...
    assert (
        eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m3", now=3.0).action
        == "human"
    )
    # ...but after a long idle gap the budget resets and it's autonomous again (first turn).
    revived = eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m4", now=500.0)
    assert revived.action == "autonomous"
    assert revived.first_turn is True


def test_end_and_end_contact_clear_state() -> None:
    eng = ExchangeEngine()
    eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m1", now=1.0)
    eng.decide(status="inbox", contact="b@h", context_id="c2", message_id="m1", now=1.0)
    assert eng.end("c1") is True
    assert eng.active_context_for("a@h") is None
    assert eng.end("c1") is False
    assert eng.end_contact("b@h") == 1
    assert eng.active() == []


def test_seen_ids_are_bounded() -> None:
    eng = ExchangeEngine()
    # Push well past the cap; the run must not grow unbounded.
    for i in range(600):
        eng.decide(status="inbox", contact="a@h", context_id="c1", message_id=f"m{i}", now=float(i))
    run = eng._runs["c1"]  # noqa: SLF001 - white-box bound check
    assert len(run.seen_ids) <= 512
