"""Shared mock Shadownet Sidecar for every host integration harness.

One canonical fake that exposes the whole Sidecar contract, so OpenClaw,
Hermes, and the Claude Code monitor all assert against identical behavior:

  /mcp                     real RFC-0002 streamable-HTTP MCP server (bare v0.2
                           tool names, structuredContent the python-sdk models
                           validate) — used by MCP-client hosts (Hermes,
                           Claude Code).
  /u/{shadow}/mcp          raw JSON-RPC tools/call compat for the current
                           OpenClaw plugin's simple client (social_* names,
                           pre-MCP shapes). Retire once OpenClaw speaks real MCP.
  /trigger-inbox-event     signs an RFC-0007 inbox.message envelope and POSTs it
                           to a target webhook URL (OpenClaw inbound).
  /_enqueue-inbox-event    queue an event the next MCP inbox_wait returns
                           (long-poll inbound: Hermes, Claude Code).

Every call lands in one ordered, assertable trace:
  GET  /_calls   -> [{seq, transport, name, arguments, ...}]
  POST /_reset   -> clear trace + queued events
  GET  /healthz
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from itertools import count
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse

SHADOWNAME = "alice@sh4dow.org"
PK = "z6MkpStubStubStubStubStubStubStubStubStubStub"
SIDECAR_ID = "mock-shadownet"

_trace: list[dict[str, Any]] = []
_events: list[dict[str, Any]] = []
_seq = count(1)


def _record(transport: str, name: str, arguments: dict[str, Any], **extra: Any) -> None:
    entry = {"seq": next(_seq), "transport": transport, "name": name, "arguments": arguments}
    entry.update(extra)
    _trace.append(entry)


# The harness reaches this server by Docker service name on an internal,
# zero-egress network, so streamable-HTTP Host/Origin (DNS-rebinding)
# protection — which otherwise allows only localhost — must be relaxed.
mcp = FastMCP(
    "shadownet-mock",
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


class Identity(BaseModel):
    shadowname: str
    pk: str
    credentials: list[Any] = []


class InboxWait(BaseModel):
    events: list[dict[str, Any]] = []
    next_event_id: str | None = None


class InboxItemModel(BaseModel):
    message_id: str
    context_id: str
    sender: str
    received_at: str
    status: str
    body: dict[str, Any]


class Inbox(BaseModel):
    items: list[InboxItemModel] = []
    next_since: str | None = None


class SendResultModel(BaseModel):
    message_id: str
    context_id: str
    status: str


class RespondResultModel(BaseModel):
    message_id: str
    status: str


@mcp.tool()
async def identity() -> Identity:
    _record("mcp", "identity", {})
    return Identity(shadowname=SHADOWNAME, pk=PK)


@mcp.tool()
async def inbox_wait(
    timeout_seconds: int | None = None, last_event_id: str | None = None
) -> InboxWait:
    _record("mcp", "inbox_wait", {"timeout_seconds": timeout_seconds, "last_event_id": last_event_id})
    # Real long-poll: hold the connection open until an event is enqueued or the
    # client's timeout elapses (capped so the mock never hangs indefinitely).
    # This exercises the adapter's held-session polling, not a fast return.
    budget = timeout_seconds if (timeout_seconds and timeout_seconds > 0) else 30
    deadline = time.monotonic() + min(budget, 30)
    while time.monotonic() < deadline:
        if _events:
            batch = list(_events)
            _events.clear()
            next_id = batch[-1].get("event_id") or last_event_id
            return InboxWait(events=batch, next_event_id=next_id)
        await asyncio.sleep(0.1)
    return InboxWait(events=[], next_event_id=last_event_id)


@mcp.tool()
async def inbox(
    limit: int = 50,
    includeReview: bool = False,
    since: str | None = None,
    contact: str | None = None,
    intent: str | None = None,
) -> Inbox:
    _record("mcp", "inbox", {"limit": limit})
    item = InboxItemModel(
        message_id="m-stub",
        context_id="c-stub",
        sender="bob@sh4dow.org",
        received_at="2026-05-31T00:00:00Z",
        status="inbox",
        body={"text": "hello from peer"},
    )
    return Inbox(items=[item])


@mcp.tool()
async def send(to: str, body: dict[str, Any], contextId: str | None = None) -> SendResultModel:
    _record("mcp", "send", {"to": to})
    return SendResultModel(
        message_id="m-out-stub", context_id=contextId or "c-out-stub", status="accepted"
    )


@mcp.tool()
async def respond(contextId: str, body: dict[str, Any]) -> RespondResultModel:
    _record("mcp", "respond", {"contextId": contextId})
    return RespondResultModel(message_id="m-resp-stub", status="accepted")


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def _jsonrpc_route(request: Request) -> JSONResponse:
    """Raw JSON-RPC tools/call compat for the current OpenClaw plugin client."""
    shadowname = request.path_params["shadowname"]
    payload = await request.json()
    tool = payload.get("params", {}).get("name")
    args = payload.get("params", {}).get("arguments", {})
    _record("jsonrpc", tool or "", args, shadowname=shadowname)

    if tool == "social_inbox":
        result: dict[str, Any] = {
            "items": [
                {
                    "id": "in-001",
                    "contactId": args.get("contact_id", "c-test"),
                    "intentId": "i-test",
                    "interaction": "urn:shadownet:int:test",
                    "payload": {"text": "hello from peer"},
                    "receivedAt": int(time.time()),
                }
            ]
        }
    elif tool == "social_send":
        result = {"intent_id": "i-out-001", "task_id": "t-out-001"}
    elif tool == "social_respond":
        result = {"task_id": "t-resp-001"}
    elif tool == "social_identity":
        result = {"did": "did:key:zPlaceholder", "shadowname": shadowname, "credentials": []}
    else:
        result = {"echo": args}

    return JSONResponse({"jsonrpc": "2.0", "id": payload.get("id"), "result": result})


async def _trigger_route(request: Request) -> JSONResponse:
    """Sign an RFC-0007 inbox.message envelope and POST it to a webhook URL."""
    cfg = await request.json()
    target_url: str = cfg["target_url"]
    secret: str = cfg["secret"]
    envelope = {
        "shadownet:v": cfg.get("version", "0.1"),
        "event": cfg.get("event", "inbox.message"),
        "event_id": cfg.get("event_id", f"e-{int(time.time() * 1000)}"),
        "occurredAt": int(time.time()),
        "data": {
            "intentId": cfg.get("intent_id", "i-test"),
            "contactId": cfg.get("contact_id", "c-test"),
            "messageId": cfg.get("message_id", f"m-{int(time.time())}"),
            "interaction": cfg.get("interaction", "urn:shadownet:int:test"),
        },
    }
    body = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig_hex = _sign(body, secret)
    headers = {
        "Content-Type": "application/json",
        "X-Shadownet-Sidecar-Sig": f"sha256={sig_hex}",
        "X-Shadownet-Sidecar-Ts": str(int(time.time())),
        "X-Shadownet-Sidecar-Id": SIDECAR_ID,
        "X-Webhook-Signature": sig_hex,
    }
    _record("webhook", envelope["event"], {"target_url": target_url, "event_id": envelope["event_id"]})
    req = urllib.request.Request(target_url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 controlled URL
            return JSONResponse({"status": resp.status, "body": resp.read().decode("utf-8", "replace")})
    except urllib.error.HTTPError as exc:
        return JSONResponse({"status": exc.code, "body": exc.read().decode("utf-8", "replace")})


async def _enqueue_route(request: Request) -> JSONResponse:
    event = await request.json()
    _events.append(event)
    return JSONResponse({"status": "enqueued", "queued": len(_events)})


async def _calls_route(_request: Request) -> JSONResponse:
    return JSONResponse(_trace)


async def _reset_route(_request: Request) -> JSONResponse:
    _trace.clear()
    _events.clear()
    return JSONResponse({"status": "reset"})


async def _healthz_route(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


app = mcp.streamable_http_app()
app.add_route("/u/{shadowname}/mcp", _jsonrpc_route, methods=["POST"])
app.add_route("/trigger-inbox-event", _trigger_route, methods=["POST"])
app.add_route("/_enqueue-inbox-event", _enqueue_route, methods=["POST"])
app.add_route("/_calls", _calls_route, methods=["GET"])
app.add_route("/_reset", _reset_route, methods=["POST"])
app.add_route("/healthz", _healthz_route, methods=["GET"])