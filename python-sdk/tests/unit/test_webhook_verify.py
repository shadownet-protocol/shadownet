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
