"""Unit tests for the Claude Code inbound monitor.

These cover the pure helpers — config resolution, output formatting,
escape rules — without spinning up a real MCP session. The full
inbox_loop is exercised by python-sdk's test_connect_session.py against
the real MCP in-memory transport, so we don't duplicate that here.

Run with::

    cd integrations/plugins/claude-code/monitors
    python -m pytest test_inbound.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Load the monitor as a module without executing main().
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import inbound  # type: ignore[import-not-found]  # noqa: E402


def test_resolve_config_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "tok")
    monkeypatch.delenv("SHADOWNET_SIDECAR_BASE_URL", raising=False)
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    monkeypatch.delenv("SHADOWNET_LONG_POLL_TIMEOUT", raising=False)
    monkeypatch.delenv("SHADOWNET_OS_NOTIFICATIONS", raising=False)

    token, base_url, timeout, os_notif = inbound._resolve_config()
    assert token == "tok"
    assert base_url == inbound.DEFAULT_BASE_URL
    assert timeout == 30
    assert os_notif is True


def test_resolve_config_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "tok")
    monkeypatch.setenv("SHADOWNET_SIDECAR_BASE_URL", "https://acme.example/")
    _, base_url, _, _ = inbound._resolve_config()
    assert base_url == "https://acme.example"


def test_resolve_config_connect_url_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "should-be-ignored")
    monkeypatch.setenv("SHADOWNET_SIDECAR_BASE_URL", "https://ignored.example")
    monkeypatch.setenv(
        "SHADOWNET_CONNECT_URL",
        "shadownet://connect?base=https://acme.example&token=t-from-url",
    )
    token, base_url, _, _ = inbound._resolve_config()
    assert token == "t-from-url"
    assert base_url == "https://acme.example"


def test_resolve_config_handoff_url_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "SHADOWNET_CONNECT_URL",
        "shadownet://connect?base=https://x.example&handoff=ABCDEFGH-1234567",
    )
    with pytest.raises(RuntimeError, match="handoff URLs require"):
        inbound._resolve_config()


def test_resolve_config_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHADOWNET_TOKEN", raising=False)
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    with pytest.raises(RuntimeError, match="SHADOWNET_TOKEN"):
        inbound._resolve_config()


def test_resolve_config_bad_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    monkeypatch.setenv("SHADOWNET_LONG_POLL_TIMEOUT", "abc")
    with pytest.raises(RuntimeError, match="must be an integer"):
        inbound._resolve_config()


def test_resolve_config_negative_timeout_clamped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    monkeypatch.setenv("SHADOWNET_LONG_POLL_TIMEOUT", "-5")
    _, _, timeout, _ = inbound._resolve_config()
    assert timeout == 1


def test_resolve_config_os_notifications_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "t")
    monkeypatch.setenv("SHADOWNET_OS_NOTIFICATIONS", "0")
    _, _, _, os_notif = inbound._resolve_config()
    assert os_notif is False


def test_truncate_short_passthrough() -> None:
    assert inbound._truncate("hi", 200) == "hi"


def test_truncate_long_replaces_tail_with_ellipsis() -> None:
    truncated = inbound._truncate("x" * 500, 200)
    assert len(truncated) == 200
    assert truncated.endswith("…")


def test_emit_claude_notification_is_one_json_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inbound._emit_claude_notification("evt-1", "alice@x", "hello world")
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["type"] == "shadownet.inbox.message"
    assert payload["event_id"] == "evt-1"
    assert payload["from"] == "alice@x"
    assert payload["summary"] == "hello world"


def test_emit_claude_notification_truncates_long_body(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inbound._emit_claude_notification("evt-1", "alice@x", "x" * 500)
    payload = json.loads(capsys.readouterr().out.strip())
    assert len(payload["summary"]) == 200
    assert payload["summary"].endswith("…")


def test_emit_claude_notification_handles_unicode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inbound._emit_claude_notification("evt-1", "alice@x", "héllo 你好")
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["summary"] == "héllo 你好"


def test_applescript_escape_handles_quotes_and_backslashes() -> None:
    out = inbound._escape_for_applescript('say "hi" \\path')
    assert out == 'say \\"hi\\" \\\\path'


def test_powershell_escape_handles_backticks_and_quotes() -> None:
    out = inbound._escape_for_powershell('say "hi" `back')
    assert out == 'say `"hi`" ``back'


def test_main_exits_silently_when_inbound_flag_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SHADOWNET_INBOUND", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_INBOUND_ENABLED", raising=False)
    assert inbound.main() == 0


def test_main_propagates_config_error_to_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHADOWNET_INBOUND", "1")
    monkeypatch.delenv("SHADOWNET_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_TOKEN", raising=False)
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    assert inbound.main() == 1


def test_claude_plugin_option_inbound_enabled_activates_monitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The userConfig-style env var also activates the monitor."""
    monkeypatch.delenv("SHADOWNET_INBOUND", raising=False)
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_INBOUND_ENABLED", "true")
    monkeypatch.delenv("SHADOWNET_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_TOKEN", raising=False)
    # Token unset, so config resolution fails -> exit 1 (NOT 0 which would
    # mean the inbound gate kept it inactive).
    assert inbound.main() == 1


def test_resolve_config_prefers_claude_plugin_option_over_shadownet_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLAUDE_PLUGIN_OPTION_TOKEN wins over SHADOWNET_TOKEN; same for endpoint."""
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_TOKEN", "from-plugin-config")
    monkeypatch.setenv("SHADOWNET_TOKEN", "from-shell-env")
    monkeypatch.setenv(
        "CLAUDE_PLUGIN_OPTION_ENDPOINT", "https://plugin-cfg.example/u/bob/mcp"
    )
    monkeypatch.setenv("SHADOWNET_SIDECAR_BASE_URL", "https://shell-env.example")
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    token, base_url, _, _ = inbound._resolve_config()
    assert token == "from-plugin-config"
    assert base_url == "https://plugin-cfg.example"  # /u/bob/mcp stripped


def test_resolve_config_falls_back_to_shadownet_env_when_userconfig_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Power-user path: shell env vars still work without Claude Code."""
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_ENDPOINT", raising=False)
    monkeypatch.setenv("SHADOWNET_TOKEN", "shell-tok")
    monkeypatch.setenv("SHADOWNET_SIDECAR_BASE_URL", "https://shell.example/")
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    token, base_url, _, _ = inbound._resolve_config()
    assert token == "shell-tok"
    assert base_url == "https://shell.example"
