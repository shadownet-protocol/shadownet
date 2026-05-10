from __future__ import annotations

import json
import time

import pytest

from shadownet.webhook.errors import (
    WebhookReplayWindowError,
    WebhookSignatureError,
    WebhookURLInvalid,
)
from shadownet.webhook.verify import (
    build_webhook_headers,
    ensure_url_allowed,
    sign_webhook,
    verify_webhook,
)


def _body() -> bytes:
    return json.dumps(
        {
            "shadownet:v": "0.1",
            "event": "inbox.message",
            "occurredAt": int(time.time()),
            "data": {"intentId": "urn:uuid:int-001"},
        }
    ).encode()


def test_verify_round_trip() -> None:
    body = _body()
    headers = build_webhook_headers(body, secret="topsecret", sidecar_id="sc-01")
    event = verify_webhook(headers, body, secret="topsecret")
    assert event.event == "inbox.message"


def test_secret_mismatch_rejected() -> None:
    body = _body()
    headers = build_webhook_headers(body, secret="topsecret", sidecar_id="sc-01")
    with pytest.raises(WebhookSignatureError):
        verify_webhook(headers, body, secret="other")


def test_replay_window() -> None:
    body = _body()
    headers = build_webhook_headers(
        body, secret="topsecret", sidecar_id="sc-01", timestamp=int(time.time()) - 1000
    )
    with pytest.raises(WebhookReplayWindowError):
        verify_webhook(headers, body, secret="topsecret")


def test_missing_headers() -> None:
    with pytest.raises(WebhookSignatureError):
        verify_webhook({}, _body(), secret="x")


def test_signature_helper_returns_hex() -> None:
    sig = sign_webhook(b"x", secret="abc")
    assert len(sig) == 64
    assert all(c in "0123456789abcdef" for c in sig)


def test_compat_header_omitted_by_default() -> None:
    """RFC-0007 §Compatibility headers — opt-in, canonical three unchanged."""
    body = _body()
    headers = build_webhook_headers(body, secret="topsecret", sidecar_id="sc-01")
    assert set(headers) == {
        "X-Shadownet-Sidecar-Sig",
        "X-Shadownet-Sidecar-Ts",
        "X-Shadownet-Sidecar-Id",
    }
    assert "X-Webhook-Signature" not in headers


def test_compat_header_is_raw_hex_when_enabled() -> None:
    """RFC-0007 §Compatibility headers — opt-in adds raw-hex X-Webhook-Signature."""
    body = _body()
    headers = build_webhook_headers(
        body, secret="topsecret", sidecar_id="sc-01", include_generic_hmac=True
    )
    assert headers["X-Webhook-Signature"] == sign_webhook(body, secret="topsecret")


def test_compat_header_matches_canonical_tail() -> None:
    """The compat header digest matches the hex tail of the canonical sig."""
    body = _body()
    headers = build_webhook_headers(
        body, secret="topsecret", sidecar_id="sc-01", include_generic_hmac=True
    )
    canonical_tail = headers["X-Shadownet-Sidecar-Sig"].removeprefix("sha256=")
    assert headers["X-Webhook-Signature"] == canonical_tail


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/webhook",
        "http://localhost:8080/inbox",
        "http://127.0.0.1/inbox",
        "http://[::1]/inbox",
    ],
)
def test_ensure_url_allowed_passes(url: str) -> None:
    ensure_url_allowed(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/webhook",
        "ftp://example.com/webhook",
        "javascript:alert(1)",
        "http://10.0.0.1/inbox",
    ],
)
def test_ensure_url_allowed_rejects(url: str) -> None:
    with pytest.raises(WebhookURLInvalid):
        ensure_url_allowed(url)
