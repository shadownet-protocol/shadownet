#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["shadownet>=0.5.0,<0.6", "keyring>=24"]
# ///
"""Shadownet inbound monitor for Claude Code.

Started automatically by Claude Code's plugin-monitor system when the
Shadownet plugin is active (see ``.claude-plugin/plugin.json`` → ``monitors``).
Each stdout line emitted by a monitor becomes a notification delivered to
Claude during the session — that's our channel for surfacing inbound A2A
messages.

Claude Code itself has no platform-adapter model, so this monitor is the
only documented inbound path. It opens an outbound MCP session against
the Shadownet sidecar's MCP endpoint and runs the v0.2 ``inbox_wait``
long-poll loop (RFC 0002 §4) from inside this process. Inbound A2A
messages get:

1. A JSON line written to stdout — Claude sees it on the next turn and
   can act autonomously (fetch detail, respond, etc.).
2. An OS notification fired via ``osascript`` (macOS), ``notify-send``
   (Linux), or PowerShell (Windows) — so the user sees it immediately
   even when not actively in Claude Code.

Inbound is ON by default. The monitor degrades to a silent no-op only
when the operator explicitly disables it (``inbound_enabled=false`` in
Claude Code's plugin config, or ``SHADOWNET_INBOUND=0`` in the shell).

Configuration env vars (mirrors the Hermes plugin):

    SHADOWNET_INBOUND               Override switch. Set to "0"/"false"/"no"/"off"
                                    to disable; any other value (or unset)
                                    leaves the monitor enabled.
    SHADOWNET_CONNECT_URL           shadow://connect?mcp=...&token=... URL.
                                    Inline form preferred; handoff form is
                                    redeemed once via the plugin proxy's
                                    keyring cache.
    SHADOWNET_TOKEN +
        SHADOWNET_MCP_ENDPOINT      Alternative to SHADOWNET_CONNECT_URL.
    SHADOWNET_LONG_POLL_TIMEOUT     Per-call long-poll timeout, default 30s.
    SHADOWNET_OS_NOTIFICATIONS      "1" (default) to fire OS notifications;
                                    "0" to surface to Claude only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys

try:
    import keyring
except ModuleNotFoundError:  # optional convenience cache; falls back to env tokens
    keyring = None  # type: ignore[assignment]

from shadownet.mcp import InboxWaitInput, ShadownetMCPClient
from shadownet.onboarding import ConnectURIError, parse_connect_uri

_KEYRING_SERVICE = "shadownet-claude-code-plugin"

logging.basicConfig(
    level=os.environ.get("SHADOWNET_LOG_LEVEL", "INFO"),
    format="[shadownet-monitor] %(levelname)s %(message)s",
    stream=sys.stderr,
)
_log = logging.getLogger("shadownet.monitor")


def _env_first(*names: str, default: str = "") -> str:
    """Return the first env var that's set, in order. Empty string → default."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _cached_access_token(handoff_code: str) -> str | None:
    if keyring is None:
        return None
    try:
        return keyring.get_password(_KEYRING_SERVICE, handoff_code)
    except Exception as exc:  # noqa: BLE001
        _log.debug("keyring read failed (%s)", exc)
        return None


def _resolve_endpoint_and_token() -> tuple[str, str, int, bool]:
    """Return ``(mcp_endpoint, access_token, long_poll_timeout, os_notif)``.

    Source precedence: CLAUDE_PLUGIN_OPTION_* > SHADOWNET_* env vars. The
    monitor never redeems handoff URIs itself; it relies on the proxy's
    keyring cache (the proxy runs first per session). If no cached token
    is found, the monitor exits with a clear error so the user knows to
    open Claude Code (or to provide an inline URI).
    """
    connect_url = _env_first("CLAUDE_PLUGIN_OPTION_CONNECT_URL", "SHADOWNET_CONNECT_URL")
    mcp_endpoint = ""
    token = ""
    if connect_url:
        parsed = parse_connect_uri(connect_url)
        mcp_endpoint = parsed.mcp_endpoint
        if parsed.is_inline:
            assert parsed.access_token is not None
            token = parsed.access_token
        else:
            assert parsed.handoff_code is not None
            cached = _cached_access_token(parsed.handoff_code)
            if not cached:
                raise RuntimeError(
                    "Handoff URI present but no cached access token. Open Claude "
                    "Code once (which spawns the MCP proxy) so the handoff is "
                    "redeemed and cached, then this monitor can read the token."
                )
            token = cached
    else:
        token = _env_first("SHADOWNET_TOKEN")
        mcp_endpoint = _env_first("SHADOWNET_MCP_ENDPOINT")

    if not token:
        raise RuntimeError(
            "No access token. Paste a shadow:// connect URL into the plugin's "
            "`connect_url` prompt at install time, or export "
            "SHADOWNET_CONNECT_URL / (SHADOWNET_TOKEN + SHADOWNET_MCP_ENDPOINT) "
            "in your shell. Mint a connect URL at https://<your-sidecar>/connect/claude-code."
        )
    if not mcp_endpoint:
        raise RuntimeError(
            "No MCP endpoint resolved. Set SHADOWNET_MCP_ENDPOINT or use a "
            "SHADOWNET_CONNECT_URL that carries the mcp= parameter."
        )

    timeout_raw = _env_first("SHADOWNET_LONG_POLL_TIMEOUT", default="30")
    try:
        timeout = max(1, int(timeout_raw))
    except ValueError as exc:
        raise RuntimeError("SHADOWNET_LONG_POLL_TIMEOUT must be an integer") from exc
    os_notif = _env_first("SHADOWNET_OS_NOTIFICATIONS", default="1") == "1"
    return mcp_endpoint, token, timeout, os_notif


def _emit_claude_notification(event_id: str, sender: str, body: str) -> None:
    """Write a single JSON line to stdout — Claude's monitor channel.

    The format is a documented contract: a JSON object with a top-level
    ``type`` field. Claude Code surfaces each stdout line verbatim into
    the session's notification stream.
    """
    line = json.dumps(
        {
            "type": "shadownet.inbox.message",
            "event_id": event_id,
            "from": sender,
            "summary": _truncate(body, 200),
        },
        ensure_ascii=False,
    )
    print(line, flush=True)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _fire_os_notification(sender: str, body: str) -> None:
    """Best-effort cross-platform OS-level toast.

    Failures are logged at WARNING but never crash the monitor — the
    Claude-side notification is the canonical channel.
    """
    title = f"Shadownet — message from {sender}"
    summary = _truncate(body, 200)
    try:
        if sys.platform == "darwin" and shutil.which("osascript"):
            _run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{_escape_for_applescript(summary)}" '
                    f'with title "{_escape_for_applescript(title)}"',
                ]
            )
        elif sys.platform.startswith("linux") and shutil.which("notify-send"):
            _run(["notify-send", title, summary])
        elif sys.platform == "win32" and shutil.which("powershell"):
            ps_script = (
                "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,"
                "ContentType=WindowsRuntime] > $null; "
                f'$title = "{_escape_for_powershell(title)}"; '
                f'$body = "{_escape_for_powershell(summary)}"; '
                'Write-Host "$title : $body"'
            )
            _run(["powershell", "-NoProfile", "-Command", ps_script])
        else:
            _log.debug("no OS-notification backend available for %s", sys.platform)
    except Exception as exc:  # noqa: BLE001 — best-effort
        _log.warning("failed to fire OS notification: %s", exc)


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=False, capture_output=True, timeout=5)  # noqa: S603


def _escape_for_applescript(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _escape_for_powershell(s: str) -> str:
    return s.replace("`", "``").replace('"', '`"')


async def _run_monitor(mcp_endpoint: str, token: str, timeout: int, os_notif: bool) -> int:
    """RFC 0002 §4 long-poll inbox_wait loop. One JSON line per event."""
    async with ShadownetMCPClient.connect(endpoint=mcp_endpoint, access_token=token) as client:
        identity = await client.identity()
        _log.info("connected as %s; starting inbox loop", identity.shadowname)
        last_event_id: str | None = None
        while True:
            try:
                result = await client.inbox_wait(
                    InboxWaitInput(timeout_seconds=timeout, last_event_id=last_event_id)
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                _log.warning("inbox_wait failed: %s — retrying in 5s", exc)
                await asyncio.sleep(5.0)
                continue
            last_event_id = result.next_event_id or last_event_id
            for event in result.events:
                event_name = event.get("event")
                if event_name != "inbox.message":
                    _log.debug("ignoring %s event", event_name)
                    continue
                data = event.get("data") or {}
                sender = data.get("from") or "unknown"
                # RFC 0002 §7: inbox.message events carry from/contextId/
                # messageId/intent + status. The body is fetched lazily via
                # the inbox tool; here we surface a placeholder so the user
                # sees the notification and the agent can fetch the body
                # when the user asks.
                summary = data.get("intent") or "(new message)"
                event_id = event.get("eventId") or event.get("event_id") or ""
                _emit_claude_notification(event_id, sender, summary)
                if os_notif:
                    _fire_os_notification(sender, summary)


_FALSY_OVERRIDES = frozenset({"0", "false", "no", "off"})


def _inbound_enabled() -> bool:
    """Default ON. Disabled only on an explicit falsy override.

    The plugin manifest declares ``inbound_enabled: true``, but Claude Code
    doesn't migrate userConfig values across plugin updates. To avoid
    telling every existing user to manually flip a toggle, we treat any
    non-explicit value as enabled, and require an explicit ``"false"`` /
    ``"0"`` to keep the monitor off.
    """
    for var in ("CLAUDE_PLUGIN_OPTION_INBOUND_ENABLED", "SHADOWNET_INBOUND"):
        value = os.environ.get(var, "").strip().lower()
        if value in _FALSY_OVERRIDES:
            return False
    return True


def main() -> int:
    if not _inbound_enabled():
        _log.info(
            "Inbound monitor disabled by explicit override "
            "(inbound_enabled=false in Claude Code's plugin config, or "
            "SHADOWNET_INBOUND=0 in the shell). Unset the override or "
            "give it any other value to re-enable."
        )
        return 0
    try:
        return asyncio.run(_resolve_and_run())
    except KeyboardInterrupt:
        _log.info("monitor stopped by signal")
        return 0


async def _resolve_and_run() -> int:
    try:
        mcp_endpoint, token, timeout, os_notif = _resolve_endpoint_and_token()
    except (RuntimeError, ConnectURIError) as exc:
        _log.error("config error: %s", exc)
        return 1
    return await _run_monitor(mcp_endpoint, token, timeout, os_notif)


if __name__ == "__main__":
    sys.exit(main())
