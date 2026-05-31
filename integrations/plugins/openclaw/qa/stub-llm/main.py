"""Deterministic OpenAI-compatible stub for the integration harness.

Lets the OpenClaw (or Hermes) gateway boot and run a turn with no GPU and no
real model credits — it returns a fixed assistant completion. Refuses to start
if a real-looking provider key is present in the environment, so the harness
can never accidentally drive an untrusted host image with live credentials.
"""

from __future__ import annotations

import json
import os
import sys
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

STUB_REPLY = "ok"
STUB_MODEL = "stub-model"

# Refuse to boot if a recognizable real provider key is set to anything other
# than a stub sentinel. The harness must be safe to run with zero real secrets.
_REAL_KEY_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
)


def _guard_no_real_keys() -> None:
    for var in _REAL_KEY_VARS:
        val = os.environ.get(var, "")
        if val and not val.startswith("sk-stub"):
            print(
                f"stub-llm: refusing to start — {var} looks like a real credential; "
                "the integration harness must use stub keys only.",
                file=sys.stderr,
            )
            raise SystemExit(2)


_guard_no_real_keys()

app = FastAPI(title="stub-llm")


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/v1/models")
async def models() -> JSONResponse:
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {
                    "id": STUB_MODEL,
                    "object": "model",
                    "created": 0,
                    "owned_by": "shadownet-harness",
                }
            ],
        }
    )


def _completion() -> dict[str, object]:
    return {
        "id": "chatcmpl-stub",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": STUB_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": STUB_REPLY},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 1, "total_tokens": 1},
    }


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(req: Request) -> JSONResponse | StreamingResponse:
    payload = await req.json()
    if payload.get("stream"):

        async def gen():
            base = {
                "id": "chatcmpl-stub",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": STUB_MODEL,
            }
            first = {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": STUB_REPLY},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(first)}\n\n"
            last = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            yield f"data: {json.dumps(last)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return JSONResponse(_completion())


@app.post("/v1/embeddings")
async def embeddings(req: Request) -> JSONResponse:
    payload = await req.json()
    raw = payload.get("input", "")
    items = raw if isinstance(raw, list) else [raw]
    return JSONResponse(
        {
            "object": "list",
            "model": payload.get("model", STUB_MODEL),
            "data": [
                {"object": "embedding", "index": i, "embedding": [0.0, 0.0, 0.0, 0.0]}
                for i, _ in enumerate(items)
            ],
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }
    )