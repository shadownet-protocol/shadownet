# RFC-0007 §social_quarantine_review

"""Quarantine review-flow conformance: accept, reject, reject_and_block.

The peer drives an unsolicited envelope to the SUT, the test then calls
``social_quarantine_review`` with each of the three decisions and asserts
the documented state transitions:

* ``accept``: the sender's task transitions to ``working`` (or beyond);
  the sender is now a contact; the original payload is delivered to
  ``social_inbox``.
* ``reject``: the sender's task transitions to ``failed`` with reason
  ``peer_declined``; no contact is created.
* ``reject_and_block``: same as reject; a subsequent envelope from the
  same DID is dropped at the gateway (no new quarantine entry).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

pytestmark = [pytest.mark.class_("sidecar"), pytest.mark.quarantine]


def _envelope(text: str, intent_id: str) -> dict[str, Any]:
    return {
        "shadownet:v": "0.1",
        "intentId": intent_id,
        "payload": {"text": text},
    }


def _message_send(payload: dict[str, Any], message_id: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "message:send",
        "params": {
            "message": {
                "role": "user",
                "messageId": message_id,
                "parts": [
                    {
                        "type": "shadownet/v1+envelope",
                        "mediaType": "application/json",
                        "data": payload,
                    }
                ],
            }
        },
    }


async def _drive_unsolicited(
    http, sidecar_url: str, sidecar_did: str, peer, text: str, nonce: str
) -> dict[str, Any]:
    """Send an unsolicited envelope and return the JSON-RPC result (Task)."""
    session_token = peer.peer.session_token_for(sidecar_did)
    vp = peer.peer.presentation_for(sidecar_did, nonce=nonce)
    intent_id = f"urn:uuid:00000000-0000-4000-9000-{text.encode().hex()[:12].ljust(12, '0')}"
    body = _message_send(_envelope(text, intent_id), message_id=f"msg-{text[:8]}")
    resp = await http.post(
        f"{sidecar_url}/a2a/message:send",
        headers={
            "Authorization": f"Bearer {session_token}",
            "X-Shadownet-Presentation": vp,
        },
        json=body,
    )
    if resp.status_code != 202:
        pytest.fail(
            f"setup: unsolicited inbound MUST be quarantined; got {resp.status_code}: "
            f"{resp.text[:200]}"
        )
    rpc = resp.json()
    return rpc.get("result") or {}


async def _find_quarantine_item(http, sidecar_url: str, text: str) -> dict[str, Any] | None:
    resp = await http.post(
        f"{sidecar_url}/mcp/social_quarantine_list",
        json={"limit": 100},
    )
    if resp.status_code == 404:
        pytest.fail(
            "Sidecar does not expose social_quarantine_list — required by RFC-0007 "
            "enterprise + cost-containment amendment."
        )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    return next((it for it in items if it.get("summary") == text), None)


@pytest.mark.network
@pytest.mark.rfc("0007", section="social_quarantine_review", requirement="accept_creates_contact")
async def test_review_accept_creates_contact_and_delivers_inbox(
    sidecar_url, http, sidecar_did, peer
) -> None:
    """``accept`` MUST add the sender to contacts AND surface the payload via social_inbox."""
    text = "accept-review-deliver-payload"
    task = await _drive_unsolicited(
        http, sidecar_url, sidecar_did, peer, text, nonce="conformance-acc-00000000000a"
    )
    assert task.get("status", {}).get("state") == "submitted"

    item = await _find_quarantine_item(http, sidecar_url, text)
    assert item is not None, "envelope did not appear in quarantine"

    review = await http.post(
        f"{sidecar_url}/mcp/social_quarantine_review",
        json={
            "quarantineId": item["quarantineId"],
            "decision": "accept",
            "displayName": "Conformance Peer",
            "grants": ["messaging"],
            "profile": {"notes": "added during conformance accept test", "priority": "normal"},
        },
    )
    if review.status_code == 404:
        pytest.fail("Sidecar does not expose social_quarantine_review — required by RFC-0007.")
    review.raise_for_status()
    body = review.json()
    assert body.get("ok") is True
    contact_id = body.get("contactId")
    assert contact_id, "accept review MUST return a contactId per RFC-0007"

    # The original payload MUST now be delivered to social_inbox.
    inbox = await http.post(f"{sidecar_url}/mcp/social_inbox", json={"limit": 100})
    inbox.raise_for_status()
    serialized = json.dumps(inbox.json().get("items", []))
    assert text in serialized, (
        "RFC-0007: accept review MUST deliver the original payload to social_inbox."
    )


@pytest.mark.network
@pytest.mark.rfc(
    "0007", section="social_quarantine_review", requirement="reject_yields_peer_declined"
)
async def test_review_reject_yields_peer_declined(sidecar_url, http, sidecar_did, peer) -> None:
    """``reject`` MUST transition the sender's task to failed/peer_declined."""
    text = "reject-review-task-failed"
    task = await _drive_unsolicited(
        http, sidecar_url, sidecar_did, peer, text, nonce="conformance-rej-00000000000a"
    )
    task_id = task.get("id")
    assert task_id, "quarantined task MUST carry an id (RFC-0006 §Async and offline)"

    item = await _find_quarantine_item(http, sidecar_url, text)
    assert item is not None

    review = await http.post(
        f"{sidecar_url}/mcp/social_quarantine_review",
        json={"quarantineId": item["quarantineId"], "decision": "reject"},
    )
    review.raise_for_status()

    # Re-fetch the sender's task and assert it failed with peer_declined.
    session_token = peer.peer.session_token_for(sidecar_did)
    vp = peer.peer.presentation_for(sidecar_did, nonce="conformance-rej-getr00000000aa")
    get_resp = await http.post(
        f"{sidecar_url}/a2a/task:get",
        headers={
            "Authorization": f"Bearer {session_token}",
            "X-Shadownet-Presentation": vp,
        },
        json={"jsonrpc": "2.0", "id": "1", "method": "task:get", "params": {"id": task_id}},
    )
    get_resp.raise_for_status()
    refreshed = get_resp.json().get("result") or {}
    state = (refreshed.get("status") or {}).get("state")
    reason = (refreshed.get("status") or {}).get("reason") or refreshed.get("reason")
    assert state == "failed", f"rejected task state MUST be 'failed'; got {state!r}"
    assert reason == "peer_declined", (
        f"rejected task reason MUST be 'peer_declined' per RFC-0006 §Errors; got {reason!r}"
    )


@pytest.mark.network
@pytest.mark.rfc("0007", section="social_quarantine_review", requirement="reject_and_block")
async def test_review_reject_and_block_drops_subsequent_inbound(
    sidecar_url, http, sidecar_did, peer
) -> None:
    """``reject_and_block`` MUST cause subsequent inbound from the same DID to be
    dropped at the gateway (no new quarantine entry)."""
    text_a = "block-this-sender-first"
    await _drive_unsolicited(
        http, sidecar_url, sidecar_did, peer, text_a, nonce="conformance-blk-1-000000000a"
    )
    item = await _find_quarantine_item(http, sidecar_url, text_a)
    assert item is not None

    review = await http.post(
        f"{sidecar_url}/mcp/social_quarantine_review",
        json={"quarantineId": item["quarantineId"], "decision": "reject_and_block"},
    )
    review.raise_for_status()

    # Second envelope from same peer (same DID). After reject_and_block it
    # MUST NOT land in quarantine.
    text_b = "second-attempt-from-blocked-sender"
    session_token = peer.peer.session_token_for(sidecar_did)
    vp = peer.peer.presentation_for(sidecar_did, nonce="conformance-blk-2-000000000a")
    body = _message_send(
        _envelope(text_b, "urn:uuid:00000000-0000-4000-a000-000000000002"),
        message_id="msg-blocked-2",
    )
    resp = await http.post(
        f"{sidecar_url}/a2a/message:send",
        headers={
            "Authorization": f"Bearer {session_token}",
            "X-Shadownet-Presentation": vp,
        },
        json=body,
    )
    # The gateway MAY return 401/403 (blocked) or 202 with a failed task —
    # the important assertion is that the item is NOT in quarantine.
    await asyncio.sleep(0.05)  # tiny wait for any async surface to settle
    after = await _find_quarantine_item(http, sidecar_url, text_b)
    assert after is None, (
        f"blocked sender's subsequent inbound MUST NOT appear in quarantine. "
        f"Got {after!r}; status {resp.status_code}."
    )
