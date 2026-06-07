"""Unit tests for the Claude Code inbound monitor (v0.2).

These cover the pure helpers — config resolution, output formatting,
escape rules — without spinning up a real MCP session. The full inbox
loop is exercised by python-sdk's MCP client tests against the in-memory
transport, so we don't duplicate that here.

Run with::

    cd integrations/plugins/claude-code/monitors
    python -m pytest test_inbound.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

# Load the monitor as a module without executing main().
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import inbound  # type: ignore[import-not-found]  # noqa: E402

MCP_ENDPOINT = "https://acme.example/mcp"
INLINE_URI = f"shadow://connect?mcp={quote(MCP_ENDPOINT, safe='')}&token=t-from-url"
HANDOFF_URI = (
    f"shadow://connect?mcp={quote(MCP_ENDPOINT, safe='')}&handoff=ABCDEFGHIJ12345678"
)


def test_resolve_config_from_shell_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "shell-tok")
    monkeypatch.setenv("SHADOWNET_MCP_ENDPOINT", MCP_ENDPOINT)
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", raising=False)
    monkeypatch.delenv("SHADOWNET_LONG_POLL_TIMEOUT", raising=False)
    monkeypatch.delenv("SHADOWNET_OS_NOTIFICATIONS", raising=False)

    endpoint, token, timeout, os_notif = inbound._resolve_endpoint_and_token()
    assert endpoint == MCP_ENDPOINT
    assert token == "shell-tok"
    assert timeout == 30
    assert os_notif is True


def test_resolve_config_inline_connect_url_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "should-be-ignored")
    monkeypatch.setenv("SHADOWNET_MCP_ENDPOINT", "https://ignored.example/mcp")
    monkeypatch.setenv("SHADOWNET_CONNECT_URL", INLINE_URI)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", raising=False)

    endpoint, token, _, _ = inbound._resolve_endpoint_and_token()
    assert endpoint == MCP_ENDPOINT
    assert token == "t-from-url"


def test_resolve_config_handoff_uses_keyring_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v0.2 monitor does NOT redeem handoff codes itself — the proxy does
    that and caches via keyring. The monitor reads the cached token; if
    it's missing, it errors with a clear pointer to open Claude Code."""
    monkeypatch.setenv("SHADOWNET_CONNECT_URL", HANDOFF_URI)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", raising=False)

    monkeypatch.setattr(inbound, "_cached_access_token", lambda _code: "cached-token")
    endpoint, token, _, _ = inbound._resolve_endpoint_and_token()
    assert endpoint == MCP_ENDPOINT
    assert token == "cached-token"


def test_resolve_config_handoff_without_cache_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHADOWNET_CONNECT_URL", HANDOFF_URI)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", raising=False)
    monkeypatch.setattr(inbound, "_cached_access_token", lambda _code: None)
    with pytest.raises(RuntimeError, match="no cached access token"):
        inbound._resolve_endpoint_and_token()


def test_resolve_config_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHADOWNET_TOKEN", raising=False)
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", raising=False)
    monkeypatch.delenv("SHADOWNET_MCP_ENDPOINT", raising=False)
    with pytest.raises(RuntimeError, match="No access token"):
        inbound._resolve_endpoint_and_token()


def test_resolve_config_missing_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "tok")
    monkeypatch.delenv("SHADOWNET_MCP_ENDPOINT", raising=False)
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", raising=False)
    with pytest.raises(RuntimeError, match="No MCP endpoint"):
        inbound._resolve_endpoint_and_token()


def test_resolve_config_prefers_claude_plugin_connect_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", INLINE_URI)
    monkeypatch.setenv(
        "SHADOWNET_CONNECT_URL",
        "shadow://connect?mcp=https://shell.example/m&token=shell",
    )

    endpoint, token, _, _ = inbound._resolve_endpoint_and_token()
    assert endpoint == MCP_ENDPOINT
    assert token == "t-from-url"


def test_resolve_config_bad_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "tok")
    monkeypatch.setenv("SHADOWNET_MCP_ENDPOINT", MCP_ENDPOINT)
    monkeypatch.setenv("SHADOWNET_LONG_POLL_TIMEOUT", "not-a-number")
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", raising=False)
    with pytest.raises(RuntimeError, match="must be an integer"):
        inbound._resolve_endpoint_and_token()


def test_resolve_config_negative_timeout_clamped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "tok")
    monkeypatch.setenv("SHADOWNET_MCP_ENDPOINT", MCP_ENDPOINT)
    monkeypatch.setenv("SHADOWNET_LONG_POLL_TIMEOUT", "-5")
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", raising=False)
    _, _, timeout, _ = inbound._resolve_endpoint_and_token()
    assert timeout == 1


def test_resolve_config_os_notifications_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHADOWNET_TOKEN", "tok")
    monkeypatch.setenv("SHADOWNET_MCP_ENDPOINT", MCP_ENDPOINT)
    monkeypatch.setenv("SHADOWNET_OS_NOTIFICATIONS", "0")
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", raising=False)
    _, _, _, os_notif = inbound._resolve_endpoint_and_token()
    assert os_notif is False


def test_truncate_short_passthrough() -> None:
    assert inbound._truncate("hello", 200) == "hello"


def test_truncate_long_replaces_tail_with_ellipsis() -> None:
    out = inbound._truncate("a" * 250, 200)
    assert len(out) == 200
    assert out.endswith("…")


def test_emit_claude_notification_is_one_json_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inbound._emit_claude_notification("evt-1", "alice@sh4dow.org", "hi there")
    captured = capsys.readouterr().out.strip().split("\n")
    assert len(captured) == 1
    parsed = json.loads(captured[0])
    assert parsed["type"] == "shadownet.inbox.message"
    assert parsed["event_id"] == "evt-1"
    assert parsed["from"] == "alice@sh4dow.org"
    assert parsed["summary"] == "hi there"


def test_emit_claude_notification_truncates_long_body(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inbound._emit_claude_notification("evt-2", "alice@sh4dow.org", "x" * 500)
    parsed = json.loads(capsys.readouterr().out.strip())
    assert len(parsed["summary"]) == 200


def test_emit_claude_notification_handles_unicode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inbound._emit_claude_notification("evt-3", "alice@sh4dow.org", "héllo 🐈")
    parsed = json.loads(capsys.readouterr().out.strip())
    assert parsed["summary"] == "héllo 🐈"


def test_applescript_escape_handles_quotes_and_backslashes() -> None:
    assert inbound._escape_for_applescript(r'a"b\c') == r"a\"b\\c"


def test_powershell_escape_handles_backticks_and_quotes() -> None:
    assert inbound._escape_for_powershell('a"b`c') == 'a`"b``c'


def test_main_runs_by_default_when_no_override_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no falsy override is set, the monitor proceeds to _resolve_and_run.
    With no credentials, _resolve_and_run returns exit 1 (config error). We
    use that as a proxy for "the monitor actually tried to run"."""
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_INBOUND_ENABLED", raising=False)
    monkeypatch.delenv("SHADOWNET_INBOUND", raising=False)
    monkeypatch.delenv("SHADOWNET_TOKEN", raising=False)
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", raising=False)
    monkeypatch.delenv("SHADOWNET_MCP_ENDPOINT", raising=False)
    assert inbound.main() == 1


def test_main_exits_silently_when_explicitly_disabled_via_plugin_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_INBOUND_ENABLED", "false")
    monkeypatch.delenv("SHADOWNET_INBOUND", raising=False)
    assert inbound.main() == 0


def test_main_exits_silently_when_explicitly_disabled_via_shell_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_INBOUND_ENABLED", raising=False)
    monkeypatch.setenv("SHADOWNET_INBOUND", "0")
    assert inbound.main() == 0


def test_inbound_enabled_helper_accepts_various_falsy_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for falsy in ("0", "false", "FALSE", "no", "off"):
        monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_INBOUND_ENABLED", raising=False)
        monkeypatch.setenv("SHADOWNET_INBOUND", falsy)
        assert inbound._inbound_enabled() is False

    for truthy in ("", "true", "1", "yes", "on"):
        monkeypatch.setenv("SHADOWNET_INBOUND", truthy)
        assert inbound._inbound_enabled() is True


def test_resolve_config_falls_back_to_shadownet_env_when_userconfig_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", raising=False)
    monkeypatch.setenv("SHADOWNET_TOKEN", "shell-tok")
    monkeypatch.setenv("SHADOWNET_MCP_ENDPOINT", "https://shell.example/mcp")
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)

    endpoint, token, _, _ = inbound._resolve_endpoint_and_token()
    assert endpoint == "https://shell.example/mcp"
    assert token == "shell-tok"
