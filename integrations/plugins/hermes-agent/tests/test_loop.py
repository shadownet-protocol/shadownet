from __future__ import annotations

from shadownet_hermes_plugin._engine import ExchangeEngine


def test_two_shadows_exchange_many_rounds_without_surfacing() -> None:
    # Two engines stand in for two Shadows passing words back and forth: every
    # inbound is handled autonomously and nothing is ever routed to a human.
    a, b = ExchangeEngine(), ExchangeEngine()
    ctx = "ctx-shared"
    for i in range(10):
        da = a.decide(
            status="inbox", contact="b@h", context_id=ctx, message_id=f"b{i}", now=2.0 * i
        )
        db = b.decide(
            status="inbox", contact="a@h", context_id=ctx, message_id=f"a{i}", now=2.0 * i + 1
        )
        assert da.action == "autonomous"
        assert db.action == "autonomous"
    assert a.active()[0]["turnCount"] == 10
    assert b.active()[0]["turnCount"] == 10


def test_directive_set_midloop_is_visible_to_the_next_turn() -> None:
    a = ExchangeEngine()
    ctx = "ctx-1"
    a.decide(status="inbox", contact="b@h", context_id=ctx, message_id="m1", now=1.0)
    a.set_directive(scope="session", target=ctx, text="stop after this round")
    assert "stop after this round" in a.directives_for("b@h", ctx)


def test_pause_halts_the_loop() -> None:
    a = ExchangeEngine()
    ctx = "ctx-1"
    a.decide(status="inbox", contact="b@h", context_id=ctx, message_id="m1", now=1.0)
    a.set_status(ctx, "paused")
    for i in range(3):
        d = a.decide(
            status="inbox", contact="b@h", context_id=ctx, message_id=f"x{i}", now=10.0 + i
        )
        assert d.action == "skip"
