#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["shadownet>=0.3.0,<0.4"]
# ///
"""Shadownet inbound monitor for Claude Code.

Started automatically by Claude Code's plugin-monitor system when the
Shadownet plugin is active (see `.claude-plugin/plugin.json` -> `monitors`).
Per the Claude Code docs, each stdout line emitted by a monitor becomes a
notification delivered to Claude during the session — that's our channel
for surfacing inbound A2A messages.

Claude Code itself has no webhook receiver model, so this monitor is the
only documented inbound path. It opens an outbound MCP session against
the Shadownet sidecar's MCP endpoint and runs the `social_inbox_wait`
long-poll loop (RFC-0007 amendment D) from inside this process. Inbound
A2A messages get:

1. A JSON line written to stdout — Claude sees it on the next turn and
   can act autonomously (fetch detail, respond, etc.).
2. An OS notification fired via `osascript` (macOS), `notify-send`
   (Linux), or PowerShell `BurntToast` (Windows) — so the user sees it
   immediately even when not actively in Claude Code.

Inbound is OPT-IN via the `SHADOWNET_INBOUND=1` env var. Without it set,
the monitor exits immediately on startup. This lets Claude Code users
who only want outbound MCP tools skip the background process entirely.

Configuration env vars (mirrors the Hermes plugin):
    SHADOWNET_INBOUND                Opt-in flag. Must be set to "1" to run.
    SHADOWNET_TOKEN                  Account bearer token (required).
    SHADOWNET_SIDECAR_BASE_URL       Sidecar base. Default: https://app.sh4dow.org
    SHADOWNET_CONNECT_URL            Optional shadownet:// connect URL; supersedes
                                     SHADOWNET_TOKEN + SHADOWNET_SIDECAR_BASE_URL.
    SHADOWNET_LONG_POLL_TIMEOUT      Per-call long-poll timeout, default 30s.
    SHADOWNET_OS_NOTIFICATIONS       "1" (default) to also fire OS notifications;
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
from typing import Any

import httpx

from shadownet.connect.bundle import fetch_integration_bundle
from shadownet.connect.session import ShadownetMCPSession
from shadownet.connect.url import parse_connect_url

DEFAULT_BASE_URL = "https://app.sh4dow.org"

logging.basicConfig(
    level=os.environ.get("SHADOWNET_LOG_LEVEL", "INFO"),
    format="[shadownet-monitor] %(levelname)s %(message)s",
    stream=sys.stderr,
)
_log = logging.getLogger("shadownet.monitor")


def _env_first(*names: str, default: str = "") -> str:
    """Return the first env var that's set, in order. Empty string -> default."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _resolve_config() -> tuple[str, str, int, bool]:
    """Resolve runtime config from Claude Code's userConfig env vars, then
    fall back to shell-style SHADOWNET_* env vars.

    Per https://code.claude.com/docs/en/plugins-reference#user-configuration,
    Claude Code exports userConfig values to plugin subprocesses as
    ``CLAUDE_PLUGIN_OPTION_<KEY>`` env vars. We prefer those so the user
    doesn't have to hand-edit a shell rc file; SHADOWNET_* env vars remain
    a fallback for power users and for environments without a Claude Code
    plugin context.
    """
    connect_url = _env_first("SHADOWNET_CONNECT_URL")
    if connect_url:
        parsed = parse_connect_url(connect_url)
        if not parsed.is_inline:
            raise RuntimeError(
                "SHADOWNET_CONNECT_URL must be inline (token=...) form for the "
                "Claude Code monitor; handoff URLs require a separate browser flow."
            )
        assert parsed.token is not None
        token = parsed.token
        # The connect URL carries the sidecar base; the monitor only needs
        # that to derive the integration-bundle and MCP endpoints.
        base_url = parsed.base_url
    else:
        token = _env_first("CLAUDE_PLUGIN_OPTION_TOKEN", "SHADOWNET_TOKEN")
        # userConfig stores the full per-tenant MCP endpoint (so users
        # paste exactly what /connect/claude-code shows); shell-env users
        # historically set just the sidecar base. Accept either.
        endpoint = _env_first("CLAUDE_PLUGIN_OPTION_ENDPOINT")
        if endpoint:
            # Strip the /u/<shadowname>/mcp suffix to recover the base.
            base_url = endpoint.rsplit("/u/", 1)[0] if "/u/" in endpoint else endpoint
        else:
            base_url = _env_first(
                "SHADOWNET_SIDECAR_BASE_URL", default=DEFAULT_BASE_URL
            )
        base_url = base_url.rstrip("/")
    if not token:
        raise RuntimeError(
            "Token not configured. Set it via Claude Code's userConfig prompt "
            "(plugin enable time) or export SHADOWNET_TOKEN in your shell; "
            "mint a token at <SHADOWNET_SIDECAR_BASE_URL>/connect/claude-code."
        )
    timeout_raw = _env_first("SHADOWNET_LONG_POLL_TIMEOUT", default="30")
    try:
        timeout = int(timeout_raw)
    except ValueError as exc:
        raise RuntimeError("SHADOWNET_LONG_POLL_TIMEOUT must be an integer") from exc
    timeout = max(1, timeout)
    os_notifications = _env_first("SHADOWNET_OS_NOTIFICATIONS", default="1") == "1"
    return token, base_url, timeout, os_notifications


def _emit_claude_notification(event_id: str, sender: str, body: str) -> None:
    """Write a single JSON line to stdout — Claude's monitor channel.

    The format is documented contract: a JSON object with a top-level
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


async def _run_monitor(token: str, base_url: str, timeout: int, os_notif: bool) -> int:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as http:
        try:
            bundle = await fetch_integration_bundle(
                http, base_url=base_url, token=token
            )
        except Exception as exc:  # noqa: BLE001
            _log.error("could not fetch integration bundle from %s: %s", base_url, exc)
            return 2
    if not bundle.supports_inbox_wait:
        _log.error(
            "sidecar at %s does not advertise 'inbox-wait' capability; "
            "the monitor requires RFC-0007 amendment D",
            base_url,
        )
        return 3

    _log.info("connected as %s; starting inbox loop", bundle.shadowname)
    async with ShadownetMCPSession(
        base_url=base_url, shadowname=bundle.shadowname, token=token
    ) as session:

        async def handle(event: Any) -> None:
            if event.event != "inbox.message":
                _log.debug("ignoring %s event", event.event)
                return
            data = event.data or {}
            sender = data.get("from") or data.get("contactId") or "unknown"
            body = data.get("body") or ""
            _emit_claude_notification(event.event_id, sender, body)
            if os_notif:
                _fire_os_notification(sender, body)

        await session.inbox_loop(handle, timeout_seconds=timeout)
    return 0


def main() -> int:
    # Inbound is opt-in. Claude Code's userConfig exposes the boolean as
    # CLAUDE_PLUGIN_OPTION_INBOUND_ENABLED ("true"/"false"); shell-env
    # users set SHADOWNET_INBOUND=1. Either turns the monitor on.
    inbound_flag = (
        os.environ.get("CLAUDE_PLUGIN_OPTION_INBOUND_ENABLED", "").lower() == "true"
        or os.environ.get("SHADOWNET_INBOUND") == "1"
    )
    if not inbound_flag:
        _log.info(
            "Inbound monitor inactive (set inbound_enabled=true in Claude Code's "
            "plugin config, or export SHADOWNET_INBOUND=1, to enable)."
        )
        return 0
    try:
        token, base_url, timeout, os_notif = _resolve_config()
    except RuntimeError as exc:
        _log.error("config error: %s", exc)
        return 1
    try:
        return asyncio.run(_run_monitor(token, base_url, timeout, os_notif))
    except KeyboardInterrupt:
        _log.info("monitor stopped by signal")
        return 0


if __name__ == "__main__":
    sys.exit(main())
