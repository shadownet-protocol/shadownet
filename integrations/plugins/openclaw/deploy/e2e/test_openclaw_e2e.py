"""Tier-1 integration assertions against a real OpenClaw gateway.

Proves what unit tests cannot: that the published gateway image loads our
channel plugin from a mounted dist, resolves its per-account config, registers
the inbound webhook route, verifies the RFC-0007 HMAC against the real mock
Sidecar, and makes the outbound social_inbox MCP call. The mock signs + posts
the webhook (its /trigger-inbox-event seam) and records every MCP tool call.

Runs inside the harness's zero-egress network; all hosts are service names.
"""

from __future__ import annotations

import os
import time

import httpx

GATEWAY = os.environ["GATEWAY_URL"]
MOCK = os.environ["MOCK_URL"]
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "/shadownet/inbox")
SECRET = os.environ["ACCOUNT_SECRET"]
TARGET = f"{GATEWAY}{WEBHOOK_PATH}"


def _reset() -> None:
    httpx.post(f"{MOCK}/_reset", timeout=10)


def _calls() -> list[dict]:
    return httpx.get(f"{MOCK}/_calls", timeout=10).json()


def _trigger(secret: str, **data) -> httpx.Response:
    payload = {"target_url": TARGET, "secret": secret, **data}
    return httpx.post(f"{MOCK}/trigger-inbox-event", json=payload, timeout=20)


def test_gateway_serves_plugin_route() -> None:
    # The plugin route is registered (a bare POST is reachable, not a 404 from
    # an unmounted route). Missing signature → the plugin's own 401.
    r = httpx.post(TARGET, content=b"{}", timeout=10)
    assert r.status_code != 404, "plugin webhook route not registered on the gateway"
    assert r.status_code == 401, f"expected missing_signature 401, got {r.status_code}: {r.text}"


def test_valid_inbound_triggers_social_inbox() -> None:
    _reset()
    r = _trigger(SECRET, intent_id="i-e2e", contact_id="c-e2e", message_id="m-e2e")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == 200, f"plugin did not ACK the webhook: {r.json()}"

    # The plugin fetches the message body via social_inbox on the mock; this
    # happens just after the 200 ACK, so poll briefly.
    deadline = time.time() + 10
    calls: list[dict] = []
    while time.time() < deadline:
        calls = _calls()
        if any(c["name"] == "social_inbox" for c in calls):
            break
        time.sleep(0.3)
    assert any(c["name"] == "social_inbox" for c in calls), f"social_inbox not called; calls={calls}"


def test_bad_signature_rejected() -> None:
    _reset()
    r = _trigger("wrong-secret-deadbeef-wrong-secret-deadbeef", intent_id="i-bad", contact_id="c-bad")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == 401, f"plugin accepted a bad HMAC: {r.json()}"