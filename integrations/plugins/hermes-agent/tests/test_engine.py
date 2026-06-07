from __future__ import annotations

import pytest

from shadownet_hermes_plugin._engine import ExchangeEngine


def _view(contact: str, ctx: str, turns: int, status: str = "active") -> dict[str, object]:
    return {"contact": contact, "contextId": ctx, "turnCount": turns, "status": status}


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
    assert eng.active() == [_view("alice@h", "c1", 1)]


def test_subsequent_message_advances_turn_not_first() -> None:
    eng = ExchangeEngine()
    eng.decide(status="inbox", contact="alice@h", context_id="c1", message_id="m1", now=1.0)
    d2 = eng.decide(status="inbox", contact="alice@h", context_id="c1", message_id="m2", now=2.0)
    assert d2.action == "autonomous"
    assert d2.first_turn is False
    assert eng.active() == [_view("alice@h", "c1", 2)]


def test_one_contact_two_contexts_are_separate_runs() -> None:
    eng = ExchangeEngine()
    eng.decide(status="inbox", contact="alice@h", context_id="c1", message_id="m1", now=1.0)
    eng.decide(status="inbox", contact="alice@h", context_id="c2", message_id="m2", now=1.0)
    assert {v["contextId"] for v in eng.active()} == {"c1", "c2"}


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


def test_contact_budget_caps_across_distinct_contexts(monkeypatch: pytest.MonkeyPatch) -> None:
    # The fan-out regression: a peer minting a fresh contextId each round must not
    # reset the budget. Three turns spread over three contexts exhaust the cap; a
    # fourth (on yet another new context) is handed to the human.
    monkeypatch.setenv("SHADOWNET_MAX_CONTACT_TURNS", "3")
    eng = ExchangeEngine()
    for i in range(3):
        d = eng.decide(
            status="inbox", contact="a@h", context_id=f"c{i}", message_id=f"m{i}", now=float(i)
        )
        assert d.action == "autonomous"
    capped = eng.decide(status="inbox", contact="a@h", context_id="c99", message_id="m99", now=4.0)
    assert capped.action == "human"
    assert capped.reason == "contact_max_turns"
    # A different contact still has its own budget.
    other = eng.decide(status="inbox", contact="b@h", context_id="cb", message_id="mb", now=5.0)
    assert other.action == "autonomous"


def test_contact_budget_resets_after_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_MAX_CONTACT_TURNS", "2")
    monkeypatch.setenv("SHADOWNET_AUTO_IDLE_SECONDS", "100")
    eng = ExchangeEngine()
    eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m1", now=1.0)
    eng.decide(status="inbox", contact="a@h", context_id="c2", message_id="m2", now=2.0)
    assert (
        eng.decide(status="inbox", contact="a@h", context_id="c3", message_id="m3", now=3.0).reason
        == "contact_max_turns"
    )
    revived = eng.decide(status="inbox", contact="a@h", context_id="c4", message_id="m4", now=500.0)
    assert revived.action == "autonomous"


def test_end_contact_clears_contact_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_MAX_CONTACT_TURNS", "1")
    eng = ExchangeEngine()
    eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m1", now=1.0)
    assert (
        eng.decide(status="inbox", contact="a@h", context_id="c2", message_id="m2", now=2.0).reason
        == "contact_max_turns"
    )
    eng.end_contact("a@h")
    # Budget is forgotten with the contact's runs, so a fresh exchange is autonomous again.
    assert (
        eng.decide(status="inbox", contact="a@h", context_id="c3", message_id="m3", now=3.0).action
        == "autonomous"
    )


def test_idle_resets_turn_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_MAX_AUTO_TURNS", "2")
    monkeypatch.setenv("SHADOWNET_AUTO_IDLE_SECONDS", "100")
    eng = ExchangeEngine()
    eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m1", now=1.0)
    eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m2", now=2.0)
    assert (
        eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m3", now=3.0).action
        == "human"
    )
    revived = eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m4", now=500.0)
    assert revived.action == "autonomous"
    assert revived.first_turn is True


def test_paused_exchange_is_skipped() -> None:
    eng = ExchangeEngine()
    eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m1", now=1.0)
    assert eng.set_status("c1", "paused") is True
    d = eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m2", now=2.0)
    assert d.action == "skip"
    assert d.reason == "paused"
    eng.set_status("c1", "active")
    resumed = eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m3", now=3.0)
    assert resumed.action == "autonomous"


def test_end_and_end_contact_clear_state() -> None:
    eng = ExchangeEngine()
    eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m1", now=1.0)
    eng.decide(status="inbox", contact="b@h", context_id="c2", message_id="m1", now=1.0)
    assert eng.end("c1") is True
    assert eng.end("c1") is False
    assert {v["contextId"] for v in eng.active()} == {"c2"}
    assert eng.end_contact("b@h") == 1
    assert eng.active() == []


def test_directives_layer_global_contact_session() -> None:
    eng = ExchangeEngine()
    eng.set_directive(scope="global", text="be brief")
    eng.set_directive(scope="contact", target="a@h", text="formal with alice")
    eng.set_directive(scope="session", target="c1", text="wrap up")
    out = eng.directives_for("a@h", "c1")
    assert "be brief" in out and "formal with alice" in out and "wrap up" in out
    other = eng.directives_for("b@h", "c2")
    assert "be brief" in other
    assert "formal with alice" not in other and "wrap up" not in other


def test_directive_clear_with_empty_text() -> None:
    eng = ExchangeEngine()
    eng.set_directive(scope="contact", target="a@h", text="x")
    eng.set_directive(scope="contact", target="a@h", text="")
    assert eng.directives_for("a@h", "c1") == ""


def test_unknown_directive_scope_raises() -> None:
    eng = ExchangeEngine()
    with pytest.raises(ValueError):
        eng.set_directive(scope="bogus", text="x")


def test_directives_persist_across_instances() -> None:
    ExchangeEngine().set_directive(scope="global", text="standing rule")
    # A fresh engine over the same HERMES_HOME reloads the persisted directive.
    assert "standing rule" in ExchangeEngine().directives_for("a@h", "c1")


def test_seen_ids_are_bounded() -> None:
    eng = ExchangeEngine()
    for i in range(600):
        eng.decide(status="inbox", contact="a@h", context_id="c1", message_id=f"m{i}", now=float(i))
    run = eng._runs["c1"]  # noqa: SLF001 - white-box bound check
    assert len(run.seen_ids) <= 512
