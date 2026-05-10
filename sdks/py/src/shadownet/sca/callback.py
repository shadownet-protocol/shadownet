from __future__ import annotations

import hashlib
import hmac
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from shadownet.errors import ShadownetError

# RFC-0004 §Callbacks. Sidecar receives:
#   X-SCA-Callback-Sig: sha256=<hex HMAC-SHA256 of body, key=sessionId>
#   X-SCA-Callback-Ts:  <unix timestamp>
# Reject if X-SCA-Callback-Ts differs from local time by more than 5 minutes.

DEFAULT_CALLBACK_SKEW_SECONDS = 5 * 60

__all__ = [
    "DEFAULT_CALLBACK_SKEW_SECONDS",
    "CallbackEvent",
    "CallbackReplayWindowError",
    "CallbackSignatureError",
    "build_callback_headers",
    "sign_callback",
    "verify_callback",
]


class CallbackSignatureError(ShadownetError):
    """The SCA callback HMAC did not verify."""


class CallbackReplayWindowError(ShadownetError):
    """The SCA callback timestamp is outside the ±5min replay window."""


class CallbackEvent(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    shadownet_v: Literal["0.1"] = Field(alias="shadownet:v")
    session_id: str = Field(alias="sessionId")
    status: Literal["pending", "ready", "failed", "expired"]


def sign_callback(body: bytes, *, session_id: str) -> str:
    """Return the hex HMAC-SHA256 (no ``sha256=`` prefix)."""
    return hmac.new(session_id.encode(), body, hashlib.sha256).hexdigest()


def build_callback_headers(
    body: bytes, *, session_id: str, timestamp: int | None = None
) -> dict[str, str]:
    ts = timestamp if timestamp is not None else int(time.time())
    return {
        "X-SCA-Callback-Sig": f"sha256={sign_callback(body, session_id=session_id)}",
        "X-SCA-Callback-Ts": str(ts),
    }


def verify_callback(
    headers: dict[str, str] | None,
    body: bytes,
    *,
    session_id: str,
    now: int | None = None,
    max_skew_seconds: int = DEFAULT_CALLBACK_SKEW_SECONDS,
) -> CallbackEvent:
    """Verify HMAC + replay window, return the parsed event payload."""
    headers = _normalize_headers(headers or {})
    sig_header = headers.get("x-sca-callback-sig")
    ts_header = headers.get("x-sca-callback-ts")
    if not sig_header or not ts_header:
        raise CallbackSignatureError("missing X-SCA-Callback-Sig or X-SCA-Callback-Ts header")
    if not sig_header.startswith("sha256="):
        raise CallbackSignatureError("X-SCA-Callback-Sig must start with 'sha256='")
    expected = sign_callback(body, session_id=session_id)
    if not hmac.compare_digest(sig_header.removeprefix("sha256="), expected):
        raise CallbackSignatureError("X-SCA-Callback-Sig does not match expected HMAC")
    try:
        ts = int(ts_header)
    except ValueError as exc:
        raise CallbackSignatureError("X-SCA-Callback-Ts is not an integer") from exc
    moment = now if now is not None else int(time.time())
    if abs(moment - ts) > max_skew_seconds:
        raise CallbackReplayWindowError(
            f"callback timestamp skew {abs(moment - ts)}s exceeds {max_skew_seconds}s"
        )
    import json

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise CallbackSignatureError(f"callback body is not JSON: {exc}") from exc
    return CallbackEvent.model_validate(payload)


def _normalize_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}
