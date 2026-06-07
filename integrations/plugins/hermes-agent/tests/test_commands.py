from __future__ import annotations

from pathlib import Path

import pytest

from shadownet_hermes_plugin import _commands


def test_build_specs_returns_seven_commands() -> None:
    """All seven /shadownet-* commands have name + handler + description."""
    from tests.conftest import FakeCtx

    ctx = FakeCtx()
    specs = _commands.build_slash_command_specs(ctx)
    assert len(specs) == 7
    names = {s["name"] for s in specs}
    assert names == {
        "shadownet-setup",
        "shadownet-inbox",
        "shadownet-reach-out",
        "shadownet-coordinate",
        "shadownet-pay",
        "shadownet-status",
        "shadownet-logout",
    }
    for spec in specs:
        assert callable(spec["handler"])
        assert spec["description"]


def test_register_slash_commands_calls_register_command_seven_times() -> None:
    """register_slash_commands fans out to ctx.register_command seven times."""
    from tests.conftest import FakeCtx

    ctx = FakeCtx()
    count = _commands.register_slash_commands(ctx)
    assert count == 7
    assert len(ctx.commands) == 7


def test_skill_handler_dispatches_to_skill_view() -> None:
    """A skill-backed slash command dispatches `skill_view` with the namespaced name."""
    from tests.conftest import FakeCtx

    ctx = FakeCtx()
    ctx.set_dispatch_return("# Shadownet Inbox\n…")
    _commands.register_slash_commands(ctx)
    inbox_handler = next(c.handler for c in ctx.commands if c.name == "shadownet-inbox")
    result = inbox_handler("")
    assert result == "# Shadownet Inbox\n…"
    assert ctx.dispatched == [("skill_view", {"name": "shadownet:shadownet-inbox"})]


def test_status_handler_returns_cli_status_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/shadownet-status delegates to the CLI status implementation."""
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    from tests.conftest import FakeCtx

    ctx = FakeCtx()
    _commands.register_slash_commands(ctx)
    status_handler = next(c.handler for c in ctx.commands if c.name == "shadownet-status")
    result = status_handler("")
    assert result is not None
    assert "shadownet plugin status" in result


def test_logout_handler_returns_cli_logout_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/shadownet-logout returns the CLI's logout message (no-op path)."""
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    from tests.conftest import FakeCtx

    ctx = FakeCtx()
    _commands.register_slash_commands(ctx)
    logout_handler = next(c.handler for c in ctx.commands if c.name == "shadownet-logout")
    result = logout_handler("")
    assert result is not None
    assert "shadownet" in result.lower()


def test_skill_handler_handles_dispatch_failure() -> None:
    """If dispatch_tool raises, the handler returns an error string instead of propagating."""
    from tests.conftest import FakeCtx

    class _RaisingCtx(FakeCtx):
        def dispatch_tool(self, name: str, args: dict, *, parent_agent: object = None) -> object:
            raise RuntimeError("registry boom")

    ctx = _RaisingCtx()
    _commands.register_slash_commands(ctx)
    handler = next(c.handler for c in ctx.commands if c.name == "shadownet-setup")
    result = handler("")
    assert result is not None
    assert "could not load" in result
