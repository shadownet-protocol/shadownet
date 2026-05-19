#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["shadownet>=0.3.1,<0.4", "keyring>=24"]
# ///
"""Shadownet MCP stdio<->HTTP+SSE proxy for Claude Code.

Claude Code's `.mcp.json` supports two transport types: `http` (direct) and
stdio (`command` + `args`). HTTP is simpler but the plugin's `userConfig`
prompts can only set verbatim strings — there's no way to derive both
endpoint + token from a single paste. The user ends up with three prompts.

This proxy lets the user paste a single `shadownet://connect?...` URL.
The plugin's `userConfig` collapses to one `connect_url` field. `.mcp.json`
declares an stdio MCP server pointing at this script with the URL exported
as `SHADOWNET_CONNECT_URL`. At session start:

1. Parse the connect URL via `shadownet.connect.url.parse_connect_url` —
   the same parser used by the Hermes plugin and the Claude Code monitor.
2. One bootstrap call to `<base>/v1/account/me/integration-bundle` to
   discover the per-tenant MCP endpoint (RFC-0008).
3. Open an HTTP+SSE MCP session against that endpoint with the parsed
   token as Bearer.
4. Bridge every JSON-RPC message between Claude Code (stdio) and the
   sidecar (HTTP+SSE) until either side closes.

Latency cost: one process spawn + one bundle fetch per session start.
Per-message overhead is buffer copies in two anyio streams — negligible.

Exits non-zero on misconfiguration so Claude Code reports the MCP server
failed cleanly:

  1 — SHADOWNET_CONNECT_URL not set
  2 — URL didn't parse
  3 — handoff URL could not be redeemed (consumed, expired, sidecar down)
  4 — couldn't reach the bundle endpoint
  5 — bundle didn't list mcp_endpoint

Handoff URLs (RFC-0008 §Handoff form) are redeemed exactly once and the
resulting token is cached via shadownet.connect.FileTokenStore. On
subsequent session starts the proxy reads the cached token instead of
re-redeeming — so Claude Code's "re-read userConfig on every spawn"
model works against single-use codes without burning a code each turn.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import httpx
from mcp.client.streamable_http import streamable_http_client
from mcp.server.stdio import stdio_server
from mcp.shared._httpx_utils import create_mcp_http_client

from shadownet.connect.bundle import fetch_integration_bundle
from shadownet.connect.errors import ConnectURLInvalid
from shadownet.connect.redeem import HandoffRedemptionError, redeem_connect_url
from shadownet.connect.tokens import default_token_store

# Stdout is reserved for JSON-RPC; all logging goes to stderr where Claude
# Code's MCP server-output panel will display it for diagnostics.
logging.basicConfig(
    level=os.environ.get("SHADOWNET_LOG_LEVEL", "WARNING"),
    format="[shadownet-mcp-proxy] %(levelname)s %(message)s",
    stream=sys.stderr,
)
_log = logging.getLogger("shadownet.mcp_proxy")


async def _pump(source, dest, label: str) -> None:
    """Forward every message from one anyio stream to another until close."""
    try:
        async for msg in source:
            await dest.send(msg)
    except (BrokenPipeError, ConnectionResetError) as exc:
        _log.debug("%s closed: %s", label, exc)
    except Exception as exc:  # noqa: BLE001
        # We intentionally don't crash the whole proxy on a single bridge
        # error — that would tear down the other direction too and Claude
        # Code couldn't see the diagnostic. Log and exit this pump only.
        _log.warning("%s pump error: %s", label, exc)


async def _resolve_mcp_endpoint(base_url: str, token: str) -> tuple[str, str]:
    """Return ``(mcp_endpoint, shadowname)`` from the per-tenant bundle."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as http:
        bundle = await fetch_integration_bundle(http, base_url=base_url, token=token)
    if not bundle.mcp_endpoint:
        raise RuntimeError("bundle response did not include mcp_endpoint")
    return bundle.mcp_endpoint, bundle.shadowname


async def _run() -> int:
    # Claude Code exports every userConfig value to plugin subprocesses as
    # `CLAUDE_PLUGIN_OPTION_<KEY>` (plugins-reference §User configuration,
    # line 481). `${user_config.X}` substitution into the .mcp.json env block
    # works for non-sensitive fields, but as of Claude Code 2.1.x the
    # substitution doesn't resolve `sensitive: true` keychain-backed values
    # — that path lands the proxy with an empty env var. The CLAUDE_PLUGIN_*
    # form does cover sensitive values, so we prefer it and fall back to the
    # shell-env name for power-user / non-Claude contexts.
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

    # Resolve both inline and handoff URLs through the SDK. For handoff
    # URLs the first call redeems the single-use code and caches the
    # resulting token in the OS keychain (or 0600 JSON if keyring isn't
    # available); subsequent proxy starts read the cached token without
    # re-redeeming.
    store = default_token_store()
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as http:
        try:
            base, token = await redeem_connect_url(http, url, store=store)
        except ConnectURLInvalid as exc:
            _log.error("could not parse shadownet:// URL: %s", exc)
            return 2
        except HandoffRedemptionError as exc:
            _log.error("handoff redemption failed: %s", exc)
            return 3

    try:
        mcp_endpoint, shadowname = await _resolve_mcp_endpoint(base, token)
    except Exception as exc:  # noqa: BLE001
        _log.error("could not fetch integration bundle from %s: %s", base, exc)
        return 4

    _log.info("bridging stdio <-> %s (shadowname=%s)", mcp_endpoint, shadowname)

    http_client = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with http_client:
        async with streamable_http_client(mcp_endpoint, http_client=http_client) as (
            up_read,
            up_write,
            _get_session_id,
        ):
            async with stdio_server() as (stdio_read, stdio_write):
                # Two pumps in parallel. The proxy ends when EITHER side closes
                # — Claude Code shutting down OR the sidecar dropping the
                # session.
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
