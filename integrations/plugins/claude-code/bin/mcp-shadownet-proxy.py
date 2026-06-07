#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["shadownet>=0.5.0,<0.6", "keyring>=24"]
# ///
"""Shadownet MCP stdio<->streamable_http proxy for Claude Code.

Claude Code's ``.mcp.json`` supports two transport types: ``http`` (direct)
and stdio (``command`` + ``args``). HTTP is simpler but the plugin's
``userConfig`` prompts can only set verbatim strings — there's no way to
derive both endpoint + token from a single paste. The user ends up with
three prompts.

This proxy lets the user paste a single ``shadow://connect?...`` URL
(RFC 0003 §3). The plugin's ``userConfig`` collapses to one ``connect_url``
field. ``.mcp.json`` declares an stdio MCP server pointing at this script
with the URL exported as ``SHADOWNET_CONNECT_URL``. At session start:

1. Parse the URL via :func:`shadownet.onboarding.parse_connect_uri`.
2. For inline URIs (``token=...``): use the embedded access token directly.
   For handoff URIs (``handoff=...``): redeem once via
   :func:`shadownet.onboarding.aredeem_handoff`, cache the access token
   via :mod:`keyring` for subsequent spawns.
3. Open a streamable_http MCP session against ``parsed.mcp_endpoint`` with
   the access token as Bearer.
4. Bridge every JSON-RPC message between Claude Code (stdio) and the
   sidecar until either side closes.

Exits non-zero on misconfiguration so Claude Code surfaces the failure:

  1 — SHADOWNET_CONNECT_URL not set
  2 — URL didn't parse
  3 — handoff URL could not be redeemed (consumed, expired, sidecar down)

Handoff codes are single-use. On first run we redeem and cache; subsequent
proxy starts read the cached access token instead of re-redeeming. The
cache key includes the handoff code itself, so a new code triggers a new
redemption automatically.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import httpx
import keyring
from mcp.client.streamable_http import streamable_http_client
from mcp.server.stdio import stdio_server
from mcp.shared._httpx_utils import create_mcp_http_client

from shadownet.onboarding import (
    ConnectURIError,
    HandoffError,
    aredeem_handoff,
    parse_connect_uri,
)

# Stdout is reserved for JSON-RPC; all logging goes to stderr where Claude
# Code's MCP server-output panel will display it for diagnostics.
logging.basicConfig(
    level=os.environ.get("SHADOWNET_LOG_LEVEL", "WARNING"),
    format="[shadownet-mcp-proxy] %(levelname)s %(message)s",
    stream=sys.stderr,
)
_log = logging.getLogger("shadownet.mcp_proxy")

_KEYRING_SERVICE = "shadownet-claude-code-plugin"


def _cached_access_token(handoff_code: str) -> str | None:
    try:
        return keyring.get_password(_KEYRING_SERVICE, handoff_code)
    except Exception as exc:  # noqa: BLE001 — keyring is best-effort
        _log.debug("keyring read failed (%s); will redeem fresh", exc)
        return None


def _cache_access_token(handoff_code: str, token: str) -> None:
    try:
        keyring.set_password(_KEYRING_SERVICE, handoff_code, token)
    except Exception as exc:  # noqa: BLE001
        _log.warning("keyring write failed (%s); next spawn will re-redeem", exc)


async def _pump(source, dest, label: str) -> None:
    """Forward every message from one anyio stream to another until close."""
    try:
        async for msg in source:
            await dest.send(msg)
    except (BrokenPipeError, ConnectionResetError) as exc:
        _log.debug("%s closed: %s", label, exc)
    except Exception as exc:  # noqa: BLE001
        _log.warning("%s pump error: %s", label, exc)


async def _resolve_endpoint_and_token(connect_url: str) -> tuple[str, str]:
    """Return ``(mcp_endpoint, access_token)`` from a shadow://connect URL.

    Inline URIs are returned verbatim; handoff URIs are redeemed against
    the sidecar's RFC 0003 §4 endpoint and the resulting access token is
    keyring-cached so subsequent spawns don't re-redeem.
    """
    parsed = parse_connect_uri(connect_url)
    if parsed.is_inline:
        assert parsed.access_token is not None
        return parsed.mcp_endpoint, parsed.access_token

    assert parsed.handoff_code is not None
    cached = _cached_access_token(parsed.handoff_code)
    if cached:
        _log.debug("using cached access token for handoff code")
        return parsed.mcp_endpoint, cached

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as http:
        response = await aredeem_handoff(
            parsed.mcp_endpoint, parsed.handoff_code, client=http
        )
    _cache_access_token(parsed.handoff_code, response.access_token)
    return parsed.mcp_endpoint, response.access_token


async def _run() -> int:
    # Claude Code exports every userConfig value to plugin subprocesses as
    # ``CLAUDE_PLUGIN_OPTION_<KEY>`` (plugins-reference §User configuration).
    # ``${user_config.X}`` substitution into the .mcp.json env block works
    # for non-sensitive fields, but the CLAUDE_PLUGIN_* form also covers
    # ``sensitive: true`` values where the substitution doesn't resolve. We
    # prefer it and fall back to the shell-env name for power-user contexts.
    url = (
        os.environ.get("CLAUDE_PLUGIN_OPTION_CONNECT_URL", "").strip()
        or os.environ.get("SHADOWNET_CONNECT_URL", "").strip()
    )
    if not url:
        _log.error(
            "connect_url is empty. Expected the Claude Code plugin's "
            "`connect_url` userConfig to be set (via /plugin → Configure "
            "options or the install prompt), or SHADOWNET_CONNECT_URL "
            "exported in the shell for non-Claude contexts."
        )
        return 1

    try:
        mcp_endpoint, token = await _resolve_endpoint_and_token(url)
    except ConnectURIError as exc:
        _log.error("could not parse shadow:// URL: %s", exc)
        return 2
    except HandoffError as exc:
        _log.error("handoff redemption failed: %s", exc)
        return 3

    _log.info("bridging stdio <-> %s", mcp_endpoint)

    http_client = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with http_client:
        async with streamable_http_client(mcp_endpoint, http_client=http_client) as (
            up_read,
            up_write,
            _get_session_id,
        ):
            async with stdio_server() as (stdio_read, stdio_write):
                # Two pumps in parallel. The proxy ends when EITHER side closes —
                # Claude Code shutting down OR the sidecar dropping the session.
                await asyncio.gather(
                    _pump(stdio_read, up_write, "stdio->upstream"),
                    _pump(up_read, stdio_write, "upstream->stdio"),
                )
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
