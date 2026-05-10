from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from shadownet.webhook.errors import (
    WebhookReplayWindowError,
    WebhookSignatureError,
    WebhookURLInvalid,
)

# RFC-0007 §Inbound notifications.
#  Headers:
#    X-Shadownet-Sidecar-Sig: sha256=<hex HMAC-SHA256 of body, key=secret>
#    X-Shadownet-Sidecar-Ts:  <unix timestamp>
#    X-Shadownet-Sidecar-Id:  <opaque>
#  Replay window: ±5 minutes.

DEFAULT_WEBHOOK_SKEW_SECONDS = 5 * 60
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}

__all__ = [
    "DEFAULT_WEBHOOK_SKEW_SECONDS",
    "WebhookEvent",
    "build_webhook_headers",
    "ensure_url_allowed",
    "sign_webhook",
    "verify_webhook",
]


class WebhookEvent(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    shadownet_v: Literal["0.1"] = Field(alias="shadownet:v")
    event: str
    occurred_at: int = Field(alias="occurredAt", ge=0)
    data: dict[str, Any]


def sign_webhook(body: bytes, *, secret: str | bytes) -> str:
    """Return the hex HMAC-SHA256 (no ``sha256=`` prefix)."""
    key = secret.encode() if isinstance(secret, str) else secret
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def build_webhook_headers(
    body: bytes,
    *,
    secret: str | bytes,
    sidecar_id: str,
    timestamp: int | None = None,
) -> dict[str, str]:
    ts = timestamp if timestamp is not None else int(time.time())
    return {
        "X-Shadownet-Sidecar-Sig": f"sha256={sign_webhook(body, secret=secret)}",
        "X-Shadownet-Sidecar-Ts": str(ts),
        "X-Shadownet-Sidecar-Id": sidecar_id,
    }


def verify_webhook(
    headers: dict[str, str] | None,
    body: bytes,
    *,
    secret: str | bytes,
    now: int | None = None,
    max_skew_seconds: int = DEFAULT_WEBHOOK_SKEW_SECONDS,
) -> WebhookEvent:
    """Verify the HMAC + replay window and return the parsed event."""
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    sig_header = headers.get("x-shadownet-sidecar-sig")
    ts_header = headers.get("x-shadownet-sidecar-ts")
    if not sig_header or not ts_header:
        raise WebhookSignatureError(
            "missing X-Shadownet-Sidecar-Sig or X-Shadownet-Sidecar-Ts header"
        )
    if not sig_header.startswith("sha256="):
        raise WebhookSignatureError("X-Shadownet-Sidecar-Sig must start with 'sha256='")
    expected = sign_webhook(body, secret=secret)
    if not hmac.compare_digest(sig_header.removeprefix("sha256="), expected):
        raise WebhookSignatureError("X-Shadownet-Sidecar-Sig does not match expected HMAC")
    try:
        ts = int(ts_header)
    except ValueError as exc:
        raise WebhookSignatureError("X-Shadownet-Sidecar-Ts is not an integer") from exc
    moment = now if now is not None else int(time.time())
    if abs(moment - ts) > max_skew_seconds:
        raise WebhookReplayWindowError(
            f"webhook timestamp skew {abs(moment - ts)}s exceeds {max_skew_seconds}s"
        )
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise WebhookSignatureError(f"webhook body is not JSON: {exc}") from exc
    return WebhookEvent.model_validate(payload)


def ensure_url_allowed(url: str) -> None:
    """Reject any URL that isn't ``https://`` or ``http://localhost`` (RFC-0007)."""
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http":
        host = (parsed.hostname or "").lower()
        if host in _LOCAL_HOSTS:
            return
    raise WebhookURLInvalid(f"webhook URL must be https:// or http://localhost; got {url!r}")
