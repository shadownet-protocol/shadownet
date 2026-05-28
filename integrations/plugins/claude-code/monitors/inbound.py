#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["shadownet>=0.3.1,<0.4", "keyring>=24"]
# ///
"""Shadownet inbound monitor for Claude Code.

Started automatically by Claude Code's plugin-monitor system when the
Shadownet plugin is active (see `.claude-plugin/plugin.json` -> `monitors`).
Per the Claude Code docs, each stdout line emitted by a monitor becomes a
notification delivered to Claude during the session — that's our channel
for surfacing inbound A2A messages.

Claude Code itself has no platform adapter model, so this monitor is the
only documented inbound path. It opens an outbound MCP session against
the Shadownet sidecar's MCP endpoint and runs the `social_inbox_wait`
long-poll loop (RFC-0007 amendment D) from inside this process. Inbound
A2A messages get:

1. A JSON line written to stdout — Claude sees it on the next turn and
   can act autonomously (fetch detail, respond, etc.).
2. An OS notification fired via `osascript` (macOS), `notify-send`
   (Linux), or PowerShell `BurntToast` (Windows) — so the user sees it
   immediately even when not actively in Claude Code.

Inbound is ON by default. The monitor degrades to a silent no-op only
when the operator explicitly disables it (``inbound_enabled=false`` in
Claude Code's plugin config, or ``SHADOWNET_INBOUND=0`` in the shell).
The prior opt-in default caused every install before v0.3.2 to silently
miss inbound messages even after upgrading the plugin, because Claude
Code keeps the original userConfig value across updates.

Configuration env vars (mirrors the Hermes plugin):
    SHADOWNET_INBOUND                Override switch. Set to "0"/"false"/"no"/"off"
                                     to disable the monitor; any other value (or
                                     unset) leaves it enabled.
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
from shadownet.connect.redeem import redeem_connect_url
from shadownet.connect.session import ShadownetMCPSession
from shadownet.connect.tokens import default_token_store

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


async def _resolve_config() -> tuple[str, str, int, bool]:
    """Resolve runtime config from Claude Code's userConfig env vars, then
    fall back to shell-style SHADOWNET_* env vars.

    Per https://code.claude.com/docs/en/plugins-reference#user-configuration,
    Claude Code exports userConfig values to plugin subprocesses as
    ``CLAUDE_PLUGIN_OPTION_<KEY>`` env vars. We prefer those so the user
    doesn't have to hand-edit a shell rc file; SHADOWNET_* env vars remain
    a fallback for power users and for environments without a Claude Code
    plugin context.
    """
    # The 0.3.0 plugin's userConfig collapses to a single `connect_url`
    # field; Claude Code exposes it as CLAUDE_PLUGIN_OPTION_CONNECT_URL.
    # SHADOWNET_CONNECT_URL remains a shell-env fallback for users who
    # set it directly.
    connect_url = _env_first(
        "CLAUDE_PLUGIN_OPTION_CONNECT_URL", "SHADOWNET_CONNECT_URL"
    )
    if connect_url:
        # Inline URLs return the embedded token immediately. Handoff URLs
        # redeem the single-use code on first run and cache the resulting
        # token (OS keychain when available; 0o600 JSON fallback otherwise).
        # The proxy and monitor share the same cache, so whichever starts
        # first redeems and the other reads from cache.
        store = default_token_store()
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as http:
            base_url, token = await redeem_connect_url(http, connect_url, store=store)
    else:
        # Shell-env power-user path. The 0.3.0 plugin no longer asks for
        # token/endpoint via userConfig (they're derived from connect_url),
        # so CLAUDE_PLUGIN_OPTION_TOKEN/ENDPOINT will be empty in normal
        # Claude Code installs — only set when the user exports them
        # manually for testing.
        token = _env_first("SHADOWNET_TOKEN")
        base_url = _env_first(
            "SHADOWNET_SIDECAR_BASE_URL", default=DEFAULT_BASE_URL
        ).rstrip("/")
    if not token:
        raise RuntimeError(
            "Token not configured. Paste a shadownet:// connect URL into "
            "the plugin's `connect_url` prompt at install time, or export "
            "SHADOWNET_CONNECT_URL / SHADOWNET_TOKEN in your shell. Mint a "
            "connect URL at https://<your-sidecar>/connect/claude-code."
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


def _emit_claude_quarantine_notification(
    event_id: str,
    sender: str,
    purpose: str | None,
    summary: str,
) -> None:
    """Surface a quarantine.pending event to Claude.

    Per RFC-0006 §Cost guarantee the host agent MUST NOT auto-process
    quarantine — the notification deliberately omits any actionable
    instruction. Claude sees that an invitation exists; the user drives
    /shadownet-invitations to triage.

    The summary field is the sender-supplied payload text, truncated for
    display. It is NOT receiver-derived; quarantine.pending events from
    a conformant Sidecar carry only sender-provided strings.
    """
    line = json.dumps(
        {
            "type": "shadownet.quarantine.pending",
            "event_id": event_id,
            "from": sender,
            "purpose": purpose,
            "summary": _truncate(summary, 200),
        },
        ensure_ascii=False,
    )
    print(line, flush=True)


def _fire_os_quarantine_notification(sender: str, purpose: str | None) -> None:
    """Best-effort OS-level toast for a pending invitation.

    The body is the sender Shadowname + purpose label, NOT the
    sender-supplied free-text — quarantine summaries are surfaced inside
    Claude (and in /shadownet-invitations) where the user can decide
    what to do with them.
    """
    title = "Shadownet — pending invitation"
    label = purpose or "from unknown sender"
    body = f"{sender} ({label}) — review in /shadownet-invitations"
    _fire_os_notification(title, body, override_title=False)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _fire_os_notification(
    sender_or_title: str,
    body: str,
    *,
    override_title: bool = True,
) -> None:
    """Best-effort cross-platform OS-level toast.

    Failures are logged at WARNING but never crash the monitor — the
    Claude-side notification is the canonical channel.

    When ``override_title`` is False, ``sender_or_title`` is used as the
    full title (the quarantine path supplies its own); otherwise the
    legacy inbox-message format is used.
    """
    title = (
        f"Shadownet — message from {sender_or_title}" if override_title else sender_or_title
    )
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
            if event.event == "inbox.message":
                data = event.data or {}
                sender = data.get("from") or data.get("contactId") or "unknown"
                body = data.get("body") or ""
                _emit_claude_notification(event.event_id, sender, body)
                if os_notif:
                    _fire_os_notification(sender, body)
                return
            if event.event == "quarantine.pending":
                data = event.data or {}
                sender = (
                    data.get("senderShadowname")
                    or data.get("senderDid")
                    or "unknown sender"
                )
                purpose = data.get("purpose")
                summary = data.get("summary") or ""
                _emit_claude_quarantine_notification(
                    event.event_id, sender, purpose, summary
                )
                if os_notif:
                    _fire_os_quarantine_notification(sender, purpose)
                return
            _log.debug("ignoring %s event", event.event)

        await session.inbox_loop(handle, timeout_seconds=timeout)
    return 0


_FALSY_OVERRIDES = frozenset({"0", "false", "no", "off"})


def _inbound_enabled() -> bool:
    """Default ON. Disabled only on an explicit falsy override.

    The plugin manifest declares ``inbound_enabled: true`` (v0.3.2+), but
    Claude Code doesn't migrate userConfig values across plugin updates,
    so anyone who installed before that bump still carries the old
    ``false``. Rather than telling every existing user to manually flip
    a toggle, we treat any non-explicit value — empty env, stale stored
    "true", whatever Claude Code passes — as enabled, and require an
    explicit ``"false"``/``"0"`` to keep the monitor off.
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
        token, base_url, timeout, os_notif = await _resolve_config()
    except Exception as exc:  # noqa: BLE001
        _log.error("config error: %s", exc)
        return 1
    return await _run_monitor(token, base_url, timeout, os_notif)


if __name__ == "__main__":
    sys.exit(main())
