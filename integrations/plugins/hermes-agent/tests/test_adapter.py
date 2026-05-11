from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

# The conftest installs a fake gateway.platforms.base before this import.
from shadownet_hermes_plugin._adapter import (
    DEFAULT_BASE_URL,
    _resolve_config,
    build_adapter_class,
    check_shadownet_requirements,
)


class _PlatformConfig:
    """Minimal stand-in for Hermes's PlatformConfig."""

    def __init__(self, extra: dict[str, Any] | None = None) -> None:
        self.extra = extra or {}


def test_resolve_config_from_extras() -> None:
    cfg = _PlatformConfig(
        {
            "token": "tok-from-extras",
            "base_url": "https://acme.example",
            "long_poll_timeout_seconds": "45",
        }
    )
    token, base_url, timeout = _resolve_config(cfg)
    assert token == "tok-from-extras"
    assert base_url == "https://acme.example"
    assert timeout == 45


def test_resolve_config_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "tok-from-env")
    monkeypatch.delenv("SHADOWNET_SIDECAR_BASE_URL", raising=False)
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    token, base_url, timeout = _resolve_config(_PlatformConfig())
    assert token == "tok-from-env"
    assert base_url == DEFAULT_BASE_URL
    assert timeout == 30


def test_resolve_config_connect_url_supersedes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "ignored-tok")
    monkeypatch.setenv("SHADOWNET_SIDECAR_BASE_URL", "https://ignored.example")
    monkeypatch.setenv(
        "SHADOWNET_CONNECT_URL",
        "shadownet://connect?base=https://acme.example&token=t-from-url",
    )
    token, base_url, _ = _resolve_config(_PlatformConfig())
    assert token == "t-from-url"
    assert base_url == "https://acme.example"


def test_resolve_config_handoff_url_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "SHADOWNET_CONNECT_URL",
        "shadownet://connect?base=https://x.example&handoff=ABCDEFGH-1234567",
    )
    with pytest.raises(RuntimeError, match="handoff URLs require"):
        _resolve_config(_PlatformConfig())


def test_resolve_config_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHADOWNET_TOKEN", raising=False)
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    with pytest.raises(RuntimeError, match="requires SHADOWNET_TOKEN"):
        _resolve_config(_PlatformConfig())


def test_resolve_config_bad_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    monkeypatch.setenv("SHADOWNET_LONG_POLL_TIMEOUT_SECONDS", "abc")
    with pytest.raises(RuntimeError, match="must be an integer"):
        _resolve_config(_PlatformConfig())


def test_resolve_config_negative_timeout_clamped_to_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    monkeypatch.setenv("SHADOWNET_LONG_POLL_TIMEOUT_SECONDS", "-5")
    _, _, timeout = _resolve_config(_PlatformConfig())
    assert timeout == 1


def test_check_requirements_true_when_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    assert check_shadownet_requirements() is True


def test_check_requirements_true_when_connect_url_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SHADOWNET_TOKEN", raising=False)
    monkeypatch.setenv(
        "SHADOWNET_CONNECT_URL",
        "shadownet://connect?base=https://x&token=t",
    )
    assert check_shadownet_requirements() is True


def test_check_requirements_false_when_none_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHADOWNET_TOKEN", raising=False)
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    assert check_shadownet_requirements() is False


def test_adapter_class_constructs(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_adapter_class() returns a subclass of the fake BasePlatformAdapter."""
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    AdapterCls = build_adapter_class()
    instance = AdapterCls(_PlatformConfig({"token": "t"}))
    assert hasattr(instance, "handle_message")
    assert hasattr(instance, "_mark_connected")


async def test_on_event_dispatches_inbox_message_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only inbox.message events drive a turn at v1; others are dropped."""
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    AdapterCls = build_adapter_class()
    adapter = AdapterCls(_PlatformConfig({"token": "t"}))

    class _Event:
        def __init__(self, event_type: str) -> None:
            self.event = event_type
            self.event_id = "evt-1"
            self.data = {"from": "alice@x", "body": "hi"}

    await adapter._on_event(_Event("inbox.message"))
    await adapter._on_event(_Event("task.update"))
    await adapter._on_event(_Event("freshness.expired"))

    assert len(adapter.handled) == 1
    assert adapter.handled[0].text == "hi"
    assert adapter.handled[0].sender_id == "alice@x"


async def test_send_routes_to_social_send(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Hermes send() contract is mapped to the social_send MCP tool."""
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    AdapterCls = build_adapter_class()
    adapter = AdapterCls(_PlatformConfig({"token": "t"}))

    # Wire a fake session in place — no real network.
    fake_session = AsyncMock()
    fake_session.call_tool = AsyncMock(return_value=None)
    adapter._session = fake_session

    result = await adapter.send(chat_id="alice@x.example", text="hello")
    assert result.success is True
    fake_session.call_tool.assert_awaited_once()
    call_args = fake_session.call_tool.await_args
    assert call_args.args[0] == "social_send"
    payload = call_args.args[1]
    assert payload["contactId"] == "alice@x.example"
    assert payload["payload"]["body"] == "hello"


async def test_get_chat_info_returns_minimal_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    AdapterCls = build_adapter_class()
    adapter = AdapterCls(_PlatformConfig({"token": "t"}))
    info = await adapter.get_chat_info("alice@x.example")
    assert info == {"id": "alice@x.example", "platform": "shadownet"}


async def test_disconnect_cancels_inbox_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """disconnect() cancels the inbox loop and tears down the exit stack."""
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    AdapterCls = build_adapter_class()
    adapter = AdapterCls(_PlatformConfig({"token": "t"}))

    # Simulate a connected adapter with an in-flight task and stack.
    async def _forever() -> None:
        await asyncio.sleep(3600)

    adapter._inbox_task = asyncio.create_task(_forever())
    from contextlib import AsyncExitStack

    adapter._stack = AsyncExitStack()
    await adapter._stack.__aenter__()

    await adapter.disconnect()

    assert adapter._inbox_task.cancelled() or adapter._inbox_task.done()
    assert adapter.connected is False


def test_long_poll_timeout_env_var_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    monkeypatch.setenv("SHADOWNET_LONG_POLL_TIMEOUT_SECONDS", "5")
    AdapterCls = build_adapter_class()
    adapter = AdapterCls(_PlatformConfig({"token": "t"}))
    assert adapter._long_poll_timeout == 5


def test_app_sh4dow_org_only_in_default_constant() -> None:
    """RFC-0007 spec invariant: ``app.sh4dow.org`` is the documented default
    base URL and MAY appear once, as the value of ``DEFAULT_BASE_URL`` in
    ``_adapter.py``. It MUST NOT be embedded in any other plugin logic or
    in any other module.
    """
    from pathlib import Path

    pkg_root = Path(__file__).resolve().parent.parent / "shadownet_hermes_plugin"
    for py in pkg_root.rglob("*.py"):
        source = py.read_text(encoding="utf-8")
        occurrences = source.count("app.sh4dow.org")
        rel = py.relative_to(pkg_root.parent)
        if py.name == "_adapter.py":
            # Allowed only once, on the DEFAULT_BASE_URL line.
            assert occurrences == 1, f"unexpected occurrences in {rel}: {occurrences}"
            assert 'DEFAULT_BASE_URL = "https://app.sh4dow.org"' in source, (
                f"app.sh4dow.org appears in {rel} but not as DEFAULT_BASE_URL"
            )
        else:
            assert occurrences == 0, f"app.sh4dow.org leaked into {rel}"


def test_default_base_url_is_documented_default() -> None:
    assert DEFAULT_BASE_URL == "https://app.sh4dow.org"


def test_register_skill_paths_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bundled skills/ directory must contain all four SKILL.md files."""
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    from pathlib import Path

    skills_dir = Path(__file__).resolve().parent.parent / "skills"
    expected = {
        "shadownet-setup",
        "shadownet-reach-out",
        "shadownet-inbox",
        "shadownet-coordinate",
    }
    for name in expected:
        assert (skills_dir / name / "SKILL.md").is_file(), name


def test_register_invokes_ctx_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    """register(ctx) should register all 4 skills + 1 platform."""
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    from shadownet_hermes_plugin import register

    class FakeCtx:
        def __init__(self) -> None:
            self.skills: list[tuple[str, str]] = []
            self.platforms: list[dict[str, Any]] = []

        def register_skill(self, name: str, path: str) -> None:
            self.skills.append((name, path))

        def register_platform(self, **kwargs: Any) -> None:
            self.platforms.append(kwargs)

    ctx = FakeCtx()
    register(ctx)
    assert {name for name, _ in ctx.skills} == {
        "shadownet-setup",
        "shadownet-reach-out",
        "shadownet-inbox",
        "shadownet-coordinate",
    }
    assert len(ctx.platforms) == 1
    p = ctx.platforms[0]
    assert p["name"] == "shadownet"
    assert callable(p["adapter_factory"])
    assert callable(p["check_fn"])


# Default cleanup: each test starts with no Shadownet env vars. Tests that
# need them set use the per-test monkeypatch.setenv calls above; monkeypatch
# unwinds those automatically at end-of-test. This autouse fixture only
# clears any vars the developer may have exported in their shell.
@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "SHADOWNET_TOKEN",
        "SHADOWNET_SIDECAR_BASE_URL",
        "SHADOWNET_CONNECT_URL",
        "SHADOWNET_LONG_POLL_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(var, raising=False)
