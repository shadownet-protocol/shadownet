from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import quote

import pytest

# The conftest installs a fake gateway.platforms.base before this import.
from shadownet_hermes_plugin._adapter import (
    ACCEPT_PLAN_V1_URI,
    CONFIRM_PLAN_V1_URI,
    COORDINATE_V1_URI,
    DEFAULT_SIDECAR_BASE_URL,
    _build_coordination_inject,
    _build_task_update_inject,
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


class TestPromptBuilders:
    def test_coordinate_inject_carries_correlation_ids(self) -> None:
        text = _build_coordination_inject(
            sender="alice@x",
            sender_name="Alice",
            context_id="ctx-001",
            message_id="msg-001",
            intent=COORDINATE_V1_URI,
            body_text="grab coffee?",
            body_data={"activity": "coffee", "details": "Friday morning"},
        )
        assert "context_id: ctx-001" in text
        assert "message_id: msg-001" in text
        assert "coffee" in text.lower()
        assert "Friday morning" in text

    def test_coordination_inject_for_confirm_plan(self) -> None:
        text = _build_coordination_inject(
            sender="alice@x",
            sender_name="Alice",
            context_id="ctx-001",
            message_id="msg-001",
            intent=CONFIRM_PLAN_V1_URI,
            body_text="agreed plan",
            body_data={
                "activity": "Coffee",
                "when": "2026-05-15T10:00:00Z",
                "where": {"name": "The Daily Grind"},
            },
        )
        assert "context_id: ctx-001" in text
        assert "message_id: msg-001" in text
        assert "accept_plan_v1" in text

    def test_coordination_inject_for_accept_plan(self) -> None:
        text = _build_coordination_inject(
            sender="alice@x",
            sender_name="Alice",
            context_id="ctx-001",
            message_id="msg-001",
            intent=ACCEPT_PLAN_V1_URI,
            body_text="",
            body_data={},
        )
        assert "context_id: ctx-001" in text
        assert "ACCEPTED" in text

    def test_task_update_inject_carries_context_id(self) -> None:
        text = _build_task_update_inject("ctx-001", "task-42", "completed")
        assert "context_id: ctx-001" in text
        assert "task_id: task-42" in text
        assert "status: completed" in text


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

    async def test_coordinate_v1_self_dispatches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHADOWNET_NOTIFY_CHAT", "telegram:12345")
        adapter = self._setup(monkeypatch)
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
        assert "COORDINATION REQUEST" in msg.text
        assert "context_id: ctx-1" in msg.text
        assert msg.auto_skill == "shadownet-coordinate"

    async def test_stranger_free_form_surfaces_in_sender_session_with_inbox_skill(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A stranger (status=stranger_review), no SHADOWNET_NOTIFY_CHAT, must
        # SURFACE to the human — reach the agent through the platform pipeline,
        # in a session bound to the sender, carrying the context/message ids and
        # the body, with the shadownet-inbox skill auto-loaded. It must NOT be
        # handled autonomously and must NOT be silently suppressed.
        monkeypatch.delenv("SHADOWNET_NOTIFY_CHAT", raising=False)
        adapter = self._setup(monkeypatch, status="stranger_review")
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

        assert len(adapter.handled) == 1, "free-form inbound was suppressed, not surfaced"
        msg = adapter.handled[0]
        # Proper injected instructions: the inbox skill is auto-loaded on the turn.
        assert msg.auto_skill == "shadownet-inbox"
        # Proper context: the agent prompt carries the correlation ids + body.
        assert "context_id: ctx-1" in msg.text
        assert "message_id: msg-1" in msg.text
        assert "alice@sh4dow.org" in msg.text
        assert "Hi" in msg.text
        # Proper session: bound to the sender's DM, not a stray/global chat.
        assert msg.source.chat_id == "alice@sh4dow.org"
        assert msg.source.user_id == "alice@sh4dow.org"

    async def test_confirm_plan_v1_no_notify_target_is_silent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SHADOWNET_NOTIFY_CHAT", raising=False)
        adapter = self._setup(monkeypatch)
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
        # Without SHADOWNET_NOTIFY_CHAT: no self-dispatch, no inject.
        assert adapter.handled == []

    async def test_task_update_without_notify_is_silent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SHADOWNET_NOTIFY_CHAT", raising=False)
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
        monkeypatch.delenv("SHADOWNET_NOTIFY_CHAT", raising=False)
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
        assert "AUTONOMOUS SHADOWNET EXCHANGE" in msg.text
        assert "Hi" in msg.text
        assert msg.source.chat_id == "alice@sh4dow.org"
        # The engine now threads replies to this contact onto its contextId.
        assert adapter._engine.active_context_for("alice@sh4dow.org") == "ctx-1"

    async def test_duplicate_inbound_is_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SHADOWNET_NOTIFY_CHAT", raising=False)
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


class TestSendRoutesThroughClient:
    async def test_send_uses_typed_client_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from shadownet.mcp.tools import SendOutput

        adapter = _adapter_with_inline_config(monkeypatch)
        fake_client = MagicMock()
        fake_client.send = AsyncMock(
            return_value=SendOutput(messageId="m-1", contextId="ctx-1", status="accepted")
        )
        adapter._client = fake_client

        result = await adapter.send(chat_id="alice@sh4dow.org", content="hello")
        assert result.success is True
        assert result.message_id == "m-1"
        fake_client.send.assert_awaited_once()
        sent_input = fake_client.send.await_args.args[0]
        assert sent_input.to == "alice@sh4dow.org"
        assert sent_input.body.text == "hello"

    async def test_send_reports_sidecar_rejection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A rejected send is reported as failure, not silently swallowed as success."""
        from shadownet.mcp.tools import SendOutput

        adapter = _adapter_with_inline_config(monkeypatch)
        fake_client = MagicMock()
        fake_client.send = AsyncMock(
            return_value=SendOutput(
                messageId="m-2", contextId="ctx-1", status="rejected", error="not_contact"
            )
        )
        adapter._client = fake_client

        result = await adapter.send(chat_id="bob@x", content="hi")
        assert result.success is False
        assert result.error == "not_contact"

    async def test_send_distinct_content_not_suppressed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Distinct messages always go out — the old 120s cooldown that killed the loop is gone."""
        from shadownet.mcp.tools import SendOutput

        adapter = _adapter_with_inline_config(monkeypatch)
        fake_client = MagicMock()
        fake_client.send = AsyncMock(
            return_value=SendOutput(messageId="m", contextId="ctx-1", status="accepted")
        )
        adapter._client = fake_client

        await adapter.send(chat_id="bob@x", content="first")
        await adapter.send(chat_id="bob@x", content="second")
        assert fake_client.send.await_count == 2

    async def test_send_suppresses_exact_duplicate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An immediate identical resend is anti-echo'd within the dedup window."""
        from shadownet.mcp.tools import SendOutput

        adapter = _adapter_with_inline_config(monkeypatch)
        fake_client = MagicMock()
        fake_client.send = AsyncMock(
            return_value=SendOutput(messageId="m", contextId="ctx-1", status="accepted")
        )
        adapter._client = fake_client

        await adapter.send(chat_id="bob@x", content="same")
        await adapter.send(chat_id="bob@x", content="same")
        fake_client.send.assert_awaited_once()

    async def test_send_threads_via_respond_for_active_exchange(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When a contact has an active exchange, the reply threads via respond(contextId)."""
        from shadownet.mcp.tools import RespondOutput

        adapter = _adapter_with_inline_config(monkeypatch)
        fake_client = MagicMock()
        fake_client.send = AsyncMock()
        fake_client.respond = AsyncMock(
            return_value=RespondOutput(messageId="r-1", status="accepted")
        )
        adapter._client = fake_client
        adapter._engine.decide(
            status="inbox", contact="alice@sh4dow.org", context_id="ctx-9", message_id="m1"
        )

        result = await adapter.send(chat_id="alice@sh4dow.org", content="next word")
        assert result.success is True
        fake_client.respond.assert_awaited_once()
        assert fake_client.respond.await_args.args[0].context_id == "ctx-9"
        fake_client.send.assert_not_awaited()


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
