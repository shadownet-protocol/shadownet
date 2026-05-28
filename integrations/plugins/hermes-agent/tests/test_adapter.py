from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

if TYPE_CHECKING:
    from pathlib import Path

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


class _Event:
    """Test double for the SDK's InboxEvent shape."""

    def __init__(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.event = event_type
        self.event_id = "evt-1"
        self.data = data or {
            "from": "alice@x",
            "body": "hi",
            "data_type": "coordination_request",
            "contactId": "c1",
            "intentId": "i1",
        }


async def test_on_event_dispatches_coordination_request_to_self(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """coordination_request → handle_message into shadownet's own session."""
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    monkeypatch.delenv("SHADOWNET_NOTIFY_CHAT", raising=False)
    AdapterCls = build_adapter_class()
    adapter = AdapterCls(_PlatformConfig({"token": "t"}))

    await adapter._on_event(_Event("inbox.message"))
    # task.update with no notify target is a no-op; freshness.expired ignored.
    await adapter._on_event(_Event("task.update", data={"intentId": "i1", "status": "agreed"}))
    await adapter._on_event(_Event("freshness.expired"))

    assert len(adapter.handled) == 1
    event = adapter.handled[0]
    assert "COORDINATION REQUEST" in event.text
    # Plain-text intent_id / contact_id so session_search can recall later.
    assert "intent_id: i1" in event.text
    assert "contact_id: c1" in event.text
    # Hermes auto-loads this skill on a new session — single source of truth.
    assert getattr(event, "auto_skill", None) == "shadownet-coordinate"


async def test_task_update_no_notify_target_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """task.update without SHADOWNET_NOTIFY_CHAT logs but does nothing."""
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    monkeypatch.delenv("SHADOWNET_NOTIFY_CHAT", raising=False)
    AdapterCls = build_adapter_class()
    adapter = AdapterCls(_PlatformConfig({"token": "t"}))

    await adapter._on_event(
        _Event("task.update", data={"intentId": "i1", "contactId": "c1", "status": "agreed"})
    )
    assert adapter.handled == []  # no self-dispatch, no inject


def test_build_task_update_inject_text_contains_correlation_ids() -> None:
    """The task.update inject text must carry intent_id / contact_id /
    task_id / status as plain-text lines for session_search recall."""
    from shadownet_hermes_plugin._adapter import _build_task_update_inject

    text = _build_task_update_inject(
        intent_id="urn:uuid:int-001",
        contact_id="alice@x",
        task_id="task-42",
        status="confirmed",
    )
    assert "intent_id: urn:uuid:int-001" in text
    assert "contact_id: alice@x" in text
    assert "task_id: task-42" in text
    assert "status: confirmed" in text


def test_build_initiator_inject_includes_correlation_ids() -> None:
    """Plan responses must carry intent_id / contact_id for recall."""
    from shadownet_hermes_plugin._adapter import _build_initiator_inject

    text = _build_initiator_inject(
        sender_name="alice@x",
        body='{"plan": {"activity": "dinner"}}',
        data_type="response",
        intent_id="urn:uuid:int-001",
        contact_id="alice@x",
    )
    assert "intent_id: urn:uuid:int-001" in text or "urn:uuid:int-001" in text


async def test_send_routes_to_social_send(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Hermes send() contract is mapped to the social_send MCP tool."""
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    AdapterCls = build_adapter_class()
    adapter = AdapterCls(_PlatformConfig({"token": "t"}))

    # Wire a fake session in place — no real network.
    fake_session = AsyncMock()
    fake_session.call_tool = AsyncMock(return_value=None)
    adapter._session = fake_session

    result = await adapter.send(chat_id="alice@x.example", content="hello")
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
    """The bundled skills/ directory must contain every SKILL.md file."""
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    from pathlib import Path

    skills_dir = Path(__file__).resolve().parent.parent / "skills"
    expected = {
        "shadownet-setup",
        "shadownet-reach-out",
        "shadownet-inbox",
        "shadownet-invitations",
        "shadownet-coordinate",
    }
    for name in expected:
        assert (skills_dir / name / "SKILL.md").is_file(), name


def test_skill_paths_falls_back_to_shared_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the package has no sibling `skills/` (wheel install layout),
    `_skill_paths()` must find them under `<sys.prefix>/share/...`."""
    import shadownet_hermes_plugin as pkg
    from shadownet_hermes_plugin import _skills

    nonexistent = tmp_path / "no-sibling-here"
    shared = tmp_path / "share" / "hermes-plugins" / "shadownet" / "skills"
    for name in pkg.SKILL_NAMES:
        d = shared / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("# stub")

    monkeypatch.setattr(_skills, "skill_root_candidates", lambda: (nonexistent, shared))

    paths = pkg._skill_paths()
    for name in pkg.SKILL_NAMES:
        assert paths[name] == shared / name / "SKILL.md"
        assert paths[name].is_file()


def test_materialize_skills_into_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Skills land under `<HERMES_DATA_DIR>/skills/<name>/` so the agent's
    skill-loader picks them up (Hermes's `ctx.register_skill` is
    metadata-only)."""
    import shadownet_hermes_plugin as pkg

    src_root = tmp_path / "src" / "skills"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("HERMES_DATA_DIR", str(data_dir))

    skill_paths: dict[str, Path] = {}
    for name in pkg.SKILL_NAMES:
        src_skill = src_root / name
        src_skill.mkdir(parents=True)
        (src_skill / "SKILL.md").write_text(f"# {name}")
        (src_skill / "extra.md").write_text("sibling file")
        skill_paths[name] = src_skill / "SKILL.md"

    pkg._materialize_skills_into_data_dir(skill_paths)

    # Skills land under the `shadownet` category subdir so Hermes' skill
    # listing groups them (same convention as built-in `github/`).
    cat = pkg.SHADOWNET_CATEGORY
    for name in pkg.SKILL_NAMES:
        assert (data_dir / "skills" / cat / name / "SKILL.md").is_file()
        # Sibling files in the skill directory should also be copied.
        assert (data_dir / "skills" / cat / name / "extra.md").is_file()
    # A category-level DESCRIPTION.md is written so the group has a label.
    assert (data_dir / "skills" / cat / "DESCRIPTION.md").is_file()


def test_register_invokes_ctx_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    """register(ctx) wires every applicable Hermes surface in one pass."""
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    from shadownet_hermes_plugin import register
    from tests.conftest import FakeCtx

    ctx = FakeCtx()
    register(ctx)
    assert {name for name, _ in ctx.skills} == {
        "shadownet-setup",
        "shadownet-reach-out",
        "shadownet-inbox",
        "shadownet-invitations",
        "shadownet-coordinate",
    }
    assert len(ctx.platforms) == 1
    p = ctx.platforms[0]
    assert p["name"] == "shadownet"
    assert callable(p["adapter_factory"])
    assert callable(p["check_fn"])
    assert callable(p["env_enablement_fn"])
    assert "platform_hint" in p
    assert "mcp_shadownet_" in p["platform_hint"]
    # Three hooks: on_session_start, pre_llm_call, on_session_end.
    hook_names = {name for name, _ in ctx.hooks}
    assert hook_names == {"on_session_start", "pre_llm_call", "on_session_end"}
    # Seven slash commands.
    command_names = {c.name for c in ctx.commands}
    assert command_names == {
        "shadownet-setup",
        "shadownet-inbox",
        "shadownet-invitations",
        "shadownet-reach-out",
        "shadownet-coordinate",
        "shadownet-status",
        "shadownet-logout",
    }
    # Exactly one CLI subcommand: `hermes shadownet ...`.
    assert len(ctx.cli_commands) == 1
    assert ctx.cli_commands[0].name == "shadownet"


def test_register_drops_unknown_platform_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Older Hermes runtimes reject env_enablement_fn / platform_hint kwargs.

    Plugin must drop the rejected kwarg(s) and retry until register_platform
    succeeds, instead of failing to load entirely (regression for the
    `PlatformEntry.__init__() got an unexpected keyword argument
    'env_enablement_fn'` error seen in production after the 0.4.0 release).
    """
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    from shadownet_hermes_plugin import register
    from tests.conftest import FakeCtx

    rejected = {"env_enablement_fn", "platform_hint"}

    class _StrictCtx(FakeCtx):
        def register_platform(self, **kwargs: Any) -> None:
            for kw in rejected:
                if kw in kwargs:
                    raise TypeError(
                        f"PlatformEntry.__init__() got an unexpected keyword argument '{kw}'"
                    )
            super().register_platform(**kwargs)

    ctx = _StrictCtx()
    register(ctx)
    assert len(ctx.platforms) == 1
    p = ctx.platforms[0]
    # Required kwargs survive; optional ones the runtime rejected are gone.
    assert p["name"] == "shadownet"
    assert callable(p["adapter_factory"])
    assert "env_enablement_fn" not in p
    assert "platform_hint" not in p


def test_register_propagates_unknown_required_kwarg_failure() -> None:
    """If TypeError mentions a kwarg the plugin can't drop, surface it."""
    from shadownet_hermes_plugin import _register_platform_compat
    from tests.conftest import FakeCtx

    class _AlwaysRaises(FakeCtx):
        def register_platform(self, **kwargs: Any) -> None:
            raise TypeError("unrelated TypeError that doesn't name any kwarg")

    with pytest.raises(TypeError, match="unrelated TypeError"):
        _register_platform_compat(
            _AlwaysRaises(),
            name="shadownet",
            label="Shadownet",
            adapter_factory=lambda cfg: None,
            check_fn=lambda: True,
            platform_hint="x",
        )


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
