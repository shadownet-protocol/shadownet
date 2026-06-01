"""Standalone check that the shared mock serves every surface + a unified trace.

Validates (independently of any host) that:
  - the /mcp endpoint is a real streamable-HTTP MCP server whose structuredContent
    carries the v0.2 field names the python-sdk models validate against;
  - the /u/{shadow}/mcp JSON-RPC compat path answers the OpenClaw client;
  - both transports land in the ordered /_calls trace so drivers can assert.
"""

from __future__ import annotations

import os
import sys

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

BASE = os.environ.get("MOCK_BASE", "http://mock:8000")
ENDPOINT = f"{BASE}/mcp"


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=10) as http:
        await http.post("/_reset")

        async with streamablehttp_client(ENDPOINT) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                ident = await session.call_tool("identity", arguments={})
                assert ident.structuredContent and ident.structuredContent.get("shadowname"), ident
                wait = await session.call_tool(
                    "inbox_wait", arguments={"timeout_seconds": 1, "last_event_id": None}
                )
                sc = wait.structuredContent
                assert sc and "events" in sc and "next_event_id" in sc, sc

        jr = await http.post(
            "/u/alice@sh4dow.org/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "social_inbox", "arguments": {"contact_id": "c1"}},
            },
        )
        assert jr.json()["result"]["items"], jr.text

        # webhook surface: the trigger signs + posts an envelope and records it.
        await http.post("/trigger-inbox-event", json={"target_url": f"{BASE}/healthz", "secret": "x" * 32})

        calls = (await http.get("/_calls")).json()
        transports = {c["transport"] for c in calls}
        names = {c["name"] for c in calls}
        assert {"mcp", "jsonrpc", "webhook"} <= transports, calls
        assert {"identity", "inbox_wait", "social_inbox"} <= names, calls

    print("SHARED MOCK OK")
    print("  transports:", sorted(transports))
    print("  names:", sorted(names))


if __name__ == "__main__":
    try:
        anyio.run(main)
    except Exception as exc:  # noqa: BLE001 — surface clearly in CI logs
        print(f"SHARED MOCK FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise