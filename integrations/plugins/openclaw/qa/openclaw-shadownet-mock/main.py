"""Mock Shadownet cloud for the OpenClaw plugin Docker harness.

Two surfaces mirror what the real shadownet-cloud exposes:

* ``POST /trigger-inbox-event`` — test seam. The pytest harness POSTs here
  with ``{"target_url": "...", "secret": "..."}``; the mock generates a
  signed RFC-0007 ``inbox.message`` envelope and forwards it to the URL.
  Returns the response status/body the OpenClaw plugin returned.

* ``POST /u/{shadowname}/mcp`` — bare-bones MCP endpoint. Accepts JSON-RPC
  ``tools/call`` requests for the ten ``social_*`` tools. ``social_inbox``
  returns a canned message; everything else echoes the request. Records each
  call so the harness can assert what the plugin invoked.

Headers exactly mirror what shadownet-cloud's webhook_worker emits:
``X-Shadownet-Sidecar-Sig`` (canonical) + ``X-Webhook-Signature`` (compat) +
``X-Shadownet-Sidecar-Ts`` + ``X-Shadownet-Sidecar-Id``. This is the same
HMAC-SHA256 of the body keyed on the shared secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.request
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="shadownet-mock")
SIDECAR_ID = "mock-shadownet"

# Recorded MCP calls — pytest reads these to assert plugin behaviour.
mcp_calls: list[dict[str, Any]] = []


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@app.post("/trigger-inbox-event")
async def trigger(req: Request) -> JSONResponse:
    cfg = await req.json()
    target_url: str = cfg["target_url"]
    secret: str = cfg["secret"]
    intent_id: str = cfg.get("intent_id", "i-test")
    contact_id: str = cfg.get("contact_id", "c-test")
    message_id: str = cfg.get("message_id", f"m-{int(time.time())}")
    interaction: str = cfg.get("interaction", "urn:shadownet:int:test")

    envelope = {
        "shadownet:v": "0.1",
        "event": "inbox.message",
        "occurredAt": int(time.time()),
        "data": {
            "intentId": intent_id,
            "contactId": contact_id,
            "messageId": message_id,
            "interaction": interaction,
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

    request = urllib.request.Request(
        target_url, data=body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:  # noqa: S310 controlled URL
            return JSONResponse(
                {
                    "status": resp.status,
                    "body": resp.read().decode("utf-8", errors="replace"),
                }
            )
    except urllib.error.HTTPError as exc:
        return JSONResponse(
            {"status": exc.code, "body": exc.read().decode("utf-8", errors="replace")}
        )


@app.post("/u/{shadowname:path}/mcp")
async def mcp(shadowname: str, req: Request) -> JSONResponse:
    payload = await req.json()
    tool = payload.get("params", {}).get("name")
    args = payload.get("params", {}).get("arguments", {})
    mcp_calls.append({"tool": tool, "arguments": args, "shadowname": shadowname})

    # Canned responses per tool.
    if tool == "social_inbox":
        result = {
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
        result = {
            "did": "did:key:zPlaceholder",
            "shadowname": shadowname,
            "publicKey": {"kty": "OKP", "crv": "Ed25519", "x": "abc"},
            "credentials": [],
        }
    else:
        result = {"echo": args}

    return JSONResponse(
        {"jsonrpc": "2.0", "id": payload.get("id"), "result": result}
    )


@app.get("/_calls")
async def calls() -> JSONResponse:
    return JSONResponse(mcp_calls)


@app.post("/_reset")
async def reset() -> JSONResponse:
    mcp_calls.clear()
    return JSONResponse({"status": "reset"})


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})
