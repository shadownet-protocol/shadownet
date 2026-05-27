from __future__ import annotations

import pytest

from shadownet_hermes_plugin import _hooks


@pytest.fixture(autouse=True)
def _clear_pending_inbox() -> None:
    _hooks._pending_inbox.clear()


def test_on_session_start_records_pending_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """on_session_start populates _pending_inbox via the fetch helper."""
    monkeypatch.setattr(_hooks, "_fetch_pending_inbox_count", lambda: 3)
    _hooks.on_session_start_callback(session_id="sess-1", model="m", platform="telegram")
    assert _hooks._pending_inbox["sess-1"] == 3


def test_on_session_start_skips_when_count_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_hooks, "_fetch_pending_inbox_count", lambda: 0)
    _hooks.on_session_start_callback(session_id="sess-2", model="m", platform="telegram")
    assert "sess-2" not in _hooks._pending_inbox


def test_on_session_start_suppresses_shadownet_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synthetic shadownet sessions never receive an inbox nudge."""
    called = []
    monkeypatch.setattr(_hooks, "_fetch_pending_inbox_count", lambda: called.append(1) or 5)
    _hooks.on_session_start_callback(session_id="sess-3", model="m", platform="shadownet")
    assert called == []  # fetch was never invoked
    assert "sess-3" not in _hooks._pending_inbox


def test_pre_llm_call_injects_on_first_turn() -> None:
    """First turn with pending inbox returns a context dict."""
    _hooks._pending_inbox["sess-4"] = 2
    result = _hooks.pre_llm_call_callback(
        session_id="sess-4",
        user_message="hi",
        conversation_history=[],
        is_first_turn=True,
        model="m",
        platform="telegram",
    )
    assert isinstance(result, dict)
    assert "messages" in result["context"]
    assert "shadownet" in result["context"]
    # State cleared after first consumption.
    assert "sess-4" not in _hooks._pending_inbox


def test_pre_llm_call_observer_only_on_subsequent_turns() -> None:
    """Returns None on non-first turns (observer-only)."""
    _hooks._pending_inbox["sess-5"] = 7
    result = _hooks.pre_llm_call_callback(
        session_id="sess-5",
        user_message="next",
        conversation_history=["prior"],
        is_first_turn=False,
        model="m",
        platform="telegram",
    )
    assert result is None
    # State NOT cleared (consumption only happens on first turn).
    assert _hooks._pending_inbox["sess-5"] == 7


def test_pre_llm_call_returns_none_with_no_pending() -> None:
    """No stored count → no injection, even on the first turn."""
    result = _hooks.pre_llm_call_callback(
        session_id="sess-6",
        user_message="hi",
        conversation_history=[],
        is_first_turn=True,
        model="m",
        platform="telegram",
    )
    assert result is None


def test_on_session_end_drops_pending_entry() -> None:
    """on_session_end clears the per-session inbox count."""
    _hooks._pending_inbox["sess-7"] = 4
    _hooks.on_session_end_callback(session_id="sess-7", platform="telegram")
    assert "sess-7" not in _hooks._pending_inbox
