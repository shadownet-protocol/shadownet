from __future__ import annotations

from pathlib import Path

import pytest

from shadownet_hermes_plugin import _commands


def test_build_specs_returns_plugin_owned_commands() -> None:
    """Only plugin-owned commands (status, logout) are registered.

    The four skill-backed /shadownet-* commands are provided by the native
    skill mechanism; registering them here would shadow the real ones.
    """
    from tests.conftest import FakeCtx

    ctx = FakeCtx()
    specs = _commands.build_slash_command_specs(ctx)
    assert len(specs) == 2
    names = {s["name"] for s in specs}
    assert names == {"shadownet-status", "shadownet-logout"}
    for spec in specs:
        assert callable(spec["handler"])
        assert spec["description"]


def test_register_slash_commands_registers_plugin_owned_commands() -> None:
    """register_slash_commands fans out to ctx.register_command for each spec."""
    from tests.conftest import FakeCtx

    ctx = FakeCtx()
    count = _commands.register_slash_commands(ctx)
    assert count == 2
    assert len(ctx.commands) == 2


def test_status_handler_returns_cli_status_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/shadownet-status delegates to the CLI status implementation."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
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
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from tests.conftest import FakeCtx

    ctx = FakeCtx()
    _commands.register_slash_commands(ctx)
    logout_handler = next(c.handler for c in ctx.commands if c.name == "shadownet-logout")
    result = logout_handler("")
    assert result is not None
    assert "shadownet" in result.lower()
