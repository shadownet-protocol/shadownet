from __future__ import annotations

import json
from typing import Any

from shadownet_hermes_plugin import _bridge
from shadownet_hermes_plugin._engine import get_engine


class _RecordingCtx:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def register_tool(
        self, *, name: str, toolset: str, schema: dict, handler: Any, **_: Any
    ) -> None:
        self.tools[name] = handler


def _register() -> _RecordingCtx:
    ctx = _RecordingCtx()
    assert _bridge.register_bridge_tools(ctx) == 4
    return ctx


def test_directive_tool_sets_and_clears_engine_directive() -> None:
    tools = _register().tools
    out = tools["shadownet_directive"]({"scope": "contact", "target": "a@h", "text": "be brief"})
    assert "set" in out
    assert "be brief" in get_engine().directives_for("a@h", "c1")
    tools["shadownet_directive"]({"scope": "contact", "target": "a@h", "text": ""})
    assert get_engine().directives_for("a@h", "c1") == ""


def test_directive_tool_requires_target_for_scoped() -> None:
    out = _register().tools["shadownet_directive"]({"scope": "contact", "text": "x"})
    assert out.startswith("error")


def test_exchanges_tool_lists_active() -> None:
    tools = _register().tools
    get_engine().decide(status="inbox", contact="a@h", context_id="c1", message_id="m1", now=1.0)
    data = json.loads(tools["shadownet_exchanges"]({}))
    assert data == [{"contact": "a@h", "contextId": "c1", "turnCount": 1, "status": "active"}]


def test_control_tool_pause_resume_stop() -> None:
    tools = _register().tools
    eng = get_engine()
    eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m1", now=1.0)
    tools["shadownet_exchange_control"]({"context_id": "c1", "action": "pause"})
    paused = eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m2", now=2.0)
    assert paused.action == "skip"
    tools["shadownet_exchange_control"]({"context_id": "c1", "action": "resume"})
    resumed = eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m3", now=3.0)
    assert resumed.action == "autonomous"
    tools["shadownet_exchange_control"]({"context_id": "c1", "action": "stop"})
    assert eng.active() == []


def test_delegate_existing_thread_queues_kickoff_without_standing_directive() -> None:
    tools = _register().tools
    eng = get_engine()
    eng.decide(status="inbox", contact="a@h", context_id="c1", message_id="m1", now=1.0)
    out = tools["shadownet_delegate"]({"contact": "a@h", "instruction": "play a game"})
    assert "c1" in out
    kicks = eng.take_kickoffs()
    assert kicks == [{"target": "c1", "contact": "a@h", "instruction": "play a game"}]
    # One-shot: the instruction rides the kickoff, not a re-arming standing directive.
    assert eng.directives_for("a@h", "c1") == ""


def test_delegate_new_contact_targets_the_contact_for_a_fresh_thread() -> None:
    tools = _register().tools
    eng = get_engine()
    out = tools["shadownet_delegate"]({"contact": "new@h", "instruction": "say hi"})
    assert "new thread" in out
    kicks = eng.take_kickoffs()
    assert kicks == [{"target": "new@h", "contact": "new@h", "instruction": "say hi"}]
    assert eng.directives_for("new@h", "anything") == ""


def test_register_is_noop_without_register_tool() -> None:
    class _NoTools:
        pass

    assert _bridge.register_bridge_tools(_NoTools()) == 0
