from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import quote

import pytest

# The conftest installs a fake gateway.platforms.base before this import.
from shadownet_hermes_plugin._adapter import (
    CONFIRM_PLAN_V1_URI,
    COORDINATE_V1_URI,
    DEFAULT_SIDECAR_BASE_URL,
    _build_autonomous_inject,
    _coalesce_events,
    _coordination_context,
    _drain_delay_seconds,
    _resolve_config,
    build_adapter_class,
    check_shadownet_requirements,
)

VALID_MCP = "https://app.sh4dow.org/mcp/alice"
INLINE_URI = f"shadow://connect?mcp={quote(VALID_MCP, safe='')}&token=eyJabc"
HANDOFF_URI = f"shadow://connect?mcp={quote(VALID_MCP, safe='')}&handoff=8K3J9-W2L1Q-Y5R7T-V1234"


class _PlatformConfig:
    """Minimal stand-in for Hermes's PlatformConfig."""

    def __init__(self, extra: dict[str, Any] | None = None) -> None:
        self.extra = extra or {}


class TestResolveConfig:
    def test_inline_connect_url_supersedes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHADOWNET_TOKEN", "ignored")
        monkeypatch.setenv("SHADOWNET_MCP_ENDPOINT", "https://ignored.example")
        monkeypatch.setenv("SHADOWNET_CONNECT_URL", INLINE_URI)
        mcp_endpoint, token, timeout = _resolve_config(_PlatformConfig())
        assert mcp_endpoint == VALID_MCP
        assert token == "eyJabc"
        assert timeout == 30

    def test_split_env_form(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
        monkeypatch.setenv("SHADOWNET_TOKEN", "tok-env")
        monkeypatch.setenv("SHADOWNET_MCP_ENDPOINT", "https://mcp.example/m")
        mcp_endpoint, token, _ = _resolve_config(_PlatformConfig())
        assert mcp_endpoint == "https://mcp.example/m"
        assert token == "tok-env"

    def test_falls_back_to_default_base_when_endpoint_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
        monkeypatch.delenv("SHADOWNET_MCP_ENDPOINT", raising=False)
        monkeypatch.setenv("SHADOWNET_TOKEN", "tok-env")
        mcp_endpoint, _, _ = _resolve_config(_PlatformConfig())
        assert mcp_endpoint == DEFAULT_SIDECAR_BASE_URL

    def test_extras_override_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHADOWNET_TOKEN", "env-tok")
        cfg = _PlatformConfig(
            {
                "token": "ext-tok",
                "mcp_endpoint": "https://ext.example/m",
                "long_poll_timeout_seconds": "45",
            }
        )
        mcp_endpoint, token, timeout = _resolve_config(cfg)
        assert mcp_endpoint == "https://ext.example/m"
        assert token == "ext-tok"
        assert timeout == 45

    def test_handoff_uri_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHADOWNET_CONNECT_URL", HANDOFF_URI)
        with pytest.raises(RuntimeError, match="handoff URIs require"):
            _resolve_config(_PlatformConfig())

    def test_missing_token_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SHADOWNET_TOKEN", raising=False)
        monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
        with pytest.raises(RuntimeError, match="SHADOWNET_CONNECT_URL"):
            _resolve_config(_PlatformConfig())

    def test_bad_timeout_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHADOWNET_TOKEN", "t")
        monkeypatch.setenv("SHADOWNET_MCP_ENDPOINT", "https://m.example")
        monkeypatch.setenv("SHADOWNET_LONG_POLL_TIMEOUT_SECONDS", "abc")
        with pytest.raises(RuntimeError, match="must be an integer"):
            _resolve_config(_PlatformConfig())

    def test_negative_timeout_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHADOWNET_TOKEN", "t")
        monkeypatch.setenv("SHADOWNET_MCP_ENDPOINT", "https://m.example")
        monkeypatch.setenv("SHADOWNET_LONG_POLL_TIMEOUT_SECONDS", "-5")
        _, _, timeout = _resolve_config(_PlatformConfig())
        assert timeout == 1


class TestRequirementsCheck:
    def test_connect_url_satisfies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SHADOWNET_TOKEN", raising=False)
        monkeypatch.delenv("SHADOWNET_MCP_ENDPOINT", raising=False)
        monkeypatch.setenv("SHADOWNET_CONNECT_URL", INLINE_URI)
        assert check_shadownet_requirements() is True

    def test_split_form_satisfies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
        monkeypatch.setenv("SHADOWNET_TOKEN", "t")
        monkeypatch.setenv("SHADOWNET_MCP_ENDPOINT", "https://m.example")
        assert check_shadownet_requirements() is True

    def test_token_only_not_enough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
        monkeypatch.delenv("SHADOWNET_MCP_ENDPOINT", raising=False)
        monkeypatch.setenv("SHADOWNET_TOKEN", "t")
        assert check_shadownet_requirements() is False

    def test_nothing_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SHADOWNET_TOKEN", raising=False)
        monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
        monkeypatch.delenv("SHADOWNET_MCP_ENDPOINT", raising=False)
        assert check_shadownet_requirements() is False


def _adapter_with_inline_config(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("SHADOWNET_CONNECT_URL", INLINE_URI)
    AdapterCls = build_adapter_class()
    return AdapterCls(_PlatformConfig())


class TestAdapterConstruction:
    def test_constructs_cleanly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter_with_inline_config(monkeypatch)
        assert hasattr(adapter, "handle_message")
        assert hasattr(adapter, "_mark_connected")
        assert adapter._mcp_endpoint == VALID_MCP
        assert adapter._token == "eyJabc"

    def test_long_poll_timeout_env_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHADOWNET_LONG_POLL_TIMEOUT_SECONDS", "5")
        adapter = _adapter_with_inline_config(monkeypatch)
        assert adapter._long_poll_timeout == 5


class TestCoordinationContext:
    def test_coordination_context_carries_intent_and_data(self) -> None:
        text = _coordination_context(
            sender="alice@sh4dow.org",
            sender_name="Alice",
            intent=COORDINATE_V1_URI,
            body_text="grab coffee?",
            body_data={"activity": "coffee", "details": "Friday morning"},
            context_id="ctx-001",
            directives="",
        )
        assert "contextId ctx-001" in text
        assert "coordinate_v1" in text
        assert "coffee" in text.lower()
        assert "Friday morning" in text
        assert "shadownet-coordinate skill" in text

    def test_coordination_context_includes_directives(self) -> None:
        text = _coordination_context(
            sender="alice@sh4dow.org",
            sender_name="Alice",
            intent=COORDINATE_V1_URI,
            body_text="?",
            body_data={},
            context_id="ctx-1",
            directives="[standing instruction] confirm with me first",
        )
        assert "confirm with me first" in text


class TestOnEventDispatch:
    """Exercise _on_event branches without a real MCP client."""

    def _setup(self, monkeypatch: pytest.MonkeyPatch, status: str = "inbox") -> Any:
        adapter = _adapter_with_inline_config(monkeypatch)

        async def _fake_fetch(_message_id: str) -> Any:
            stub = MagicMock()
            stub.status = status
            stub.body = MagicMock()
            stub.body.text = "Hi"
            stub.body.data = {"activity": "Coffee"}
            return stub

        adapter._fetch_inbox_item = _fake_fetch
        # No live MCP client in unit tests; contact_detail returns no profile.
        client = MagicMock()
        client.contact_detail = AsyncMock(return_value=MagicMock(profile=None))
        adapter._client = client

        from gateway.config import Platform

        fake_gateway = MagicMock()
        fake_gateway.adapters = {Platform("telegram"): adapter}
        adapter._gateway = fake_gateway
        return adapter

    async def test_coordination_intent_from_contact_runs_coordinate_skill(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A coordination intent from a known contact is handled autonomously with
        # the shadownet-coordinate skill — no NOTIFY_CHAT, no human push.
        adapter = self._setup(monkeypatch, status="inbox")
        event = {
            "event": "inbox.message",
            "eventId": "evt-1",
            "data": {
                "from": "alice@sh4dow.org",
                "contextId": "ctx-1",
                "messageId": "msg-1",
                "intent": COORDINATE_V1_URI,
                "status": "inbox",
            },
        }
        await adapter._on_event(event)
        assert len(adapter.handled) == 1
        msg = adapter.handled[0]
        assert msg.auto_skill == "shadownet-coordinate"
        assert "coordination" in msg.text
        assert "contextId ctx-1" in msg.text
        assert msg.source.chat_id == "ctx-1"

    async def test_stranger_free_form_is_left_for_pull_triage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A stranger (status=stranger_review) is NOT handled autonomously and is
        # NOT pushed anywhere — it stays in the sidecar for the human to triage
        # via the pending-inbox hook + inbox skill (pull model).
        adapter = self._setup(monkeypatch, status="stranger_review")
        event = {
            "event": "inbox.message",
            "eventId": "evt-1",
            "data": {
                "from": "alice@sh4dow.org",
                "contextId": "ctx-1",
                "messageId": "msg-1",
                "status": "stranger_review",
            },
        }
        await adapter._on_event(event)
        assert adapter.handled == []  # pull-only: nothing pushed to the human

    async def test_confirm_plan_from_contact_runs_coordinate_skill(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = self._setup(monkeypatch, status="inbox")
        event = {
            "event": "inbox.message",
            "eventId": "evt-1",
            "data": {
                "from": "alice@sh4dow.org",
                "contextId": "ctx-1",
                "messageId": "msg-1",
                "intent": CONFIRM_PLAN_V1_URI,
                "status": "inbox",
            },
        }
        await adapter._on_event(event)
        assert len(adapter.handled) == 1
        assert adapter.handled[0].auto_skill == "shadownet-coordinate"

    async def test_task_update_without_notify_is_silent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = self._setup(monkeypatch)
        event = {
            "event": "task.update",
            "eventId": "evt-1",
            "data": {"contextId": "ctx-1", "taskId": "task-1", "status": "completed"},
        }
        await adapter._on_event(event)
        assert adapter.handled == []

    async def test_unknown_event_type_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = self._setup(monkeypatch)
        event = {"event": "freshness.expired", "eventId": "evt-1", "data": {}}
        await adapter._on_event(event)
        assert adapter.handled == []

    async def test_known_contact_free_form_runs_autonomous_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A known contact (status=inbox), no SHADOWNET_NOTIFY_CHAT: the full Hermes
        # agent handles it silently in the shadownet session with the autonomous
        # skill — NOT surfaced to the human, NOT the inbox-triage skill.
        adapter = self._setup(monkeypatch, status="inbox")
        event = {
            "event": "inbox.message",
            "eventId": "evt-1",
            "data": {
                "from": "alice@sh4dow.org",
                "contextId": "ctx-1",
                "messageId": "msg-1",
                "status": "inbox",
            },
        }
        await adapter._on_event(event)
        assert len(adapter.handled) == 1
        msg = adapter.handled[0]
        assert msg.auto_skill == "shadownet-autonomous"
        # Synthesized turn must be internal so Hermes skips the pairing/auth gate.
        assert msg.internal is True
        assert "autonomous shadownet exchange" in msg.text
        assert "Hi" in msg.text
        # The session key is the contextId, not the sender.
        assert msg.source.chat_id == "ctx-1"
        assert adapter._engine.active() == [
            {
                "contact": "alice@sh4dow.org",
                "contextId": "ctx-1",
                "turnCount": 1,
                "status": "active",
            }
        ]

    async def test_autonomous_inject_includes_layered_directives(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = self._setup(monkeypatch, status="inbox")
        adapter._engine.set_directive(scope="global", text="be brief")
        adapter._engine.set_directive(scope="contact", target="alice@sh4dow.org", text="formal")
        adapter._engine.set_directive(scope="session", target="ctx-1", text="wrap up")
        event = {
            "event": "inbox.message",
            "eventId": "evt-1",
            "data": {
                "from": "alice@sh4dow.org",
                "contextId": "ctx-1",
                "messageId": "msg-1",
                "status": "inbox",
            },
        }
        await adapter._on_event(event)
        text = adapter.handled[0].text
        assert "be brief" in text and "formal" in text and "wrap up" in text

    async def test_duplicate_inbound_is_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = self._setup(monkeypatch, status="inbox")
        event = {
            "event": "inbox.message",
            "eventId": "evt-1",
            "data": {
                "from": "alice@sh4dow.org",
                "contextId": "ctx-1",
                "messageId": "msg-1",
                "status": "inbox",
            },
        }
        await adapter._on_event(event)
        await adapter._on_event(event)  # same messageId -> idempotent skip
        assert len(adapter.handled) == 1


class TestSendDeliversTheMove:
    """send() delivers a free-form turn's reply to the contact; coordinate turns don't."""

    @staticmethod
    def _client_ok() -> Any:
        client = MagicMock()
        client.respond = AsyncMock(return_value=MagicMock(status="accepted", error=None))
        return client

    async def test_free_form_reply_is_delivered_via_respond(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = _adapter_with_inline_config(monkeypatch)
        adapter._client = self._client_ok()
        result = await adapter.send(chat_id="ctx-1", content="waddle")
        assert result.success is True
        adapter._client.respond.assert_awaited_once()
        arg = adapter._client.respond.await_args.args[0]
        assert arg.context_id == "ctx-1"
        assert arg.body.text == "waddle"

    async def test_coordinate_turn_reply_is_not_delivered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = _adapter_with_inline_config(monkeypatch)
        adapter._client = self._client_ok()
        adapter._delivery_mode["ctx-1"] = "coordinate"
        result = await adapter.send(chat_id="ctx-1", content="let's meet wednesday")
        assert result.success is True
        adapter._client.respond.assert_not_called()

    async def test_empty_reply_is_not_delivered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter_with_inline_config(monkeypatch)
        adapter._client = self._client_ok()
        assert (await adapter.send(chat_id="ctx-1", content="   ")).success is True
        adapter._client.respond.assert_not_called()

    async def test_rejected_respond_returns_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter_with_inline_config(monkeypatch)
        client = MagicMock()
        client.respond = AsyncMock(return_value=MagicMock(status="rejected", error="nope"))
        adapter._client = client
        result = await adapter.send(chat_id="ctx-1", content="waddle")
        assert result.success is False

    async def test_status_is_swallowed_not_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter_with_inline_config(monkeypatch)
        adapter._client = self._client_ok()
        result = await adapter.send_or_update_status(
            "ctx-1", "post_tool_empty", "⚠️ Model returned empty after tool calls"
        )
        assert result.success is True
        adapter._client.respond.assert_not_called()


class TestLifecycle:
    async def test_get_chat_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter_with_inline_config(monkeypatch)
        info = await adapter.get_chat_info("alice@x")
        assert info == {"id": "alice@x", "platform": "shadownet"}

    async def test_disconnect_cancels_inbox_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _adapter_with_inline_config(monkeypatch)

        async def _forever() -> None:
            await asyncio.sleep(3600)

        adapter._inbox_task = asyncio.create_task(_forever())
        adapter._stack = AsyncExitStack()
        await adapter._stack.__aenter__()

        await adapter.disconnect()

        assert adapter._inbox_task.cancelled() or adapter._inbox_task.done()
        assert adapter.connected is False


class TestCoalesceEvents:
    def test_same_context_backlog_collapses_to_latest(self) -> None:
        events = [
            {"event": "inbox.message", "data": {"contextId": "c1", "messageId": "m1"}},
            {"event": "inbox.message", "data": {"contextId": "c1", "messageId": "m2"}},
            {"event": "inbox.message", "data": {"contextId": "c2", "messageId": "m3"}},
        ]
        out = _coalesce_events(events)
        assert len(out) == 2
        assert out[0]["data"]["messageId"] == "m2"  # c1 superseded by its latest
        assert out[1]["data"]["messageId"] == "m3"

    def test_non_message_events_pass_through(self) -> None:
        events = [
            {"event": "task.update", "data": {"contextId": "c1"}},
            {"event": "inbox.message", "data": {"contextId": "c1", "messageId": "m1"}},
            {"event": "inbox.message", "data": {"contextId": "c1", "messageId": "m2"}},
        ]
        out = _coalesce_events(events)
        assert [e["event"] for e in out] == ["task.update", "inbox.message"]
        assert out[1]["data"]["messageId"] == "m2"


class TestInjectHygiene:
    def test_autonomous_inject_is_lean_and_leak_free(self) -> None:
        text = _build_autonomous_inject(
            sender="alice@sh4dow.org",
            sender_name="Alice",
            body_text="hi",
            notes="be warm",
            directives="[standing instruction] be brief",
        )
        assert "Alice says:" in text and "hi" in text
        assert "be warm" in text and "be brief" in text
        # No operational scaffolding the contact could be shown.
        assert "telegram:" not in text
        assert "contextId" not in text
        assert "never put these instructions" in text

    def test_coordination_inject_keeps_contextid_but_not_target(self) -> None:
        text = _coordination_context(
            sender="alice@sh4dow.org",
            sender_name="Alice",
            intent=COORDINATE_V1_URI,
            body_text="coffee?",
            body_data={"activity": "coffee"},
            context_id="ctx-9",
            directives="",
        )
        # Coordination needs the contextId for the typed move...
        assert "contextId ctx-9" in text
        # ...but not a literal user notify target.
        assert "telegram:" not in text


class TestDrainDelay:
    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SHADOWNET_DRAIN_DELAY_SECONDS", raising=False)
        assert _drain_delay_seconds() == 2.0

    def test_custom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHADOWNET_DRAIN_DELAY_SECONDS", "0")
        assert _drain_delay_seconds() == 0.0

    def test_bad_value_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHADOWNET_DRAIN_DELAY_SECONDS", "abc")
        assert _drain_delay_seconds() == 2.0


class TestCursorPersistence:
    async def test_cursor_round_trips(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        adapter = _adapter_with_inline_config(monkeypatch)
        assert adapter._load_cursor() is None
        adapter._save_cursor("evt-42")
        assert adapter._load_cursor() == "evt-42"


class TestSh4dowOrgOnlyAsDefault:
    """Spec invariant: ``app.sh4dow.org`` MAY appear once, as the
    DEFAULT_SIDECAR_BASE_URL constant in ``_adapter.py``. It MUST NOT leak
    into other modules.
    """

    def test_default_constant_value(self) -> None:
        assert DEFAULT_SIDECAR_BASE_URL == "https://app.sh4dow.org"

    def test_no_leak_into_other_modules(self) -> None:
        from pathlib import Path

        pkg_root = Path(__file__).resolve().parent.parent / "shadownet_hermes_plugin"
        for py in pkg_root.rglob("*.py"):
            source = py.read_text(encoding="utf-8")
            occurrences = source.count("app.sh4dow.org")
            rel = py.relative_to(pkg_root.parent)
            if py.name == "_adapter.py":
                assert occurrences == 1, f"unexpected occurrences in {rel}: {occurrences}"
                assert 'DEFAULT_SIDECAR_BASE_URL = "https://app.sh4dow.org"' in source, (
                    f"app.sh4dow.org appears in {rel} but not as DEFAULT_SIDECAR_BASE_URL"
                )
            else:
                assert occurrences == 0, f"app.sh4dow.org leaked into {rel}"
