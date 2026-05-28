# RFC-0006 §Routing and quarantine

"""Routing-defaults matrix conformance.

Drives inbound A2A traffic from the in-process peer (a foreign Shadow with
no contact relationship to the SUT) and asserts the routing decision the
SUT returns. Coverage focuses on the rows of the RFC-0006 defaults table
that don't depend on the SUT having a pre-existing contact graph entry —
which is the unknown-sender path that the cost guarantee depends on.

Rows that require a pre-existing contact (known-contact + messaging,
known-contact without grant, same-affiliation enterprise-tenant) live in
test_0007_quarantine_review.py, which adds the peer as a contact via the
review flow first.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.class_("sidecar")


def _envelope_part(payload: dict[str, object]) -> dict[str, object]:
    return {
        "type": "shadownet/v1+envelope",
        "mediaType": "application/json",
        "data": payload,
    }


def _message_send(payload: dict[str, object], message_id: str = "msg-1") -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "message:send",
        "params": {
            "message": {
                "role": "user",
                "messageId": message_id,
                "parts": [_envelope_part(payload)],
            }
        },
    }


def _free_form_envelope(text: str, hints: dict[str, object] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"text": text}
    if hints is not None:
        payload["hints"] = hints
    return {
        "shadownet:v": "0.1",
        "intentId": f"urn:uuid:00000000-0000-4000-8000-{text.encode().hex()[:12].ljust(12, '0')}",
        "payload": payload,
    }


@pytest.mark.network
@pytest.mark.quarantine
@pytest.mark.rfc("0006", section="routing", requirement="vp_invalid_dropped")
async def test_row1_vp_invalid_is_dropped(sidecar_url, http) -> None:
    """RFC-0006 §Routing: VP-invalid inbound MUST be dropped (no quarantine, no inbox)."""
    body = _message_send(_free_form_envelope("should be dropped"))
    # No Authorization header at all — the handshake rejects before routing.
    resp = await http.post(f"{sidecar_url}/a2a/message:send", json=body)
    assert resp.status_code in {401, 403}, (
        f"VP-invalid path must be dropped at the handshake; got {resp.status_code}"
    )


@pytest.mark.network
@pytest.mark.quarantine
@pytest.mark.rfc("0006", section="routing", requirement="unknown_sender_quarantined")
async def test_row5_unknown_sender_quarantined(sidecar_url, http, sidecar_did, peer) -> None:
    """Unknown sender + valid VP + no affiliation MUST land in quarantine (202 + submitted)."""
    session_token = peer.peer.session_token_for(sidecar_did)
    vp = peer.peer.presentation_for(sidecar_did, nonce="conformance-routing-1-0123456789ab")
    body = _message_send(_free_form_envelope("lorem ipsum dolor sit amet"))

    resp = await http.post(
        f"{sidecar_url}/a2a/message:send",
        headers={
            "Authorization": f"Bearer {session_token}",
            "X-Shadownet-Presentation": vp,
        },
        json=body,
    )
    assert resp.status_code == 202, (
        f"unknown-sender inbound MUST be quarantined (202); got {resp.status_code}: "
        f"{resp.text[:200]}"
    )
    rpc = resp.json()
    task = rpc.get("result") or {}
    state = (task.get("status") or {}).get("state")
    assert state == "submitted", (
        f"quarantined task state MUST be 'submitted' per RFC-0006 §Sender behavior; got {state!r}"
    )


@pytest.mark.network
@pytest.mark.quarantine
@pytest.mark.rfc("0006", section="routing", requirement="unknown_sender_not_in_inbox")
async def test_row5_unknown_sender_not_visible_in_social_inbox(
    sidecar_url, http, sidecar_did, peer
) -> None:
    """A quarantined unsolicited envelope MUST NOT appear in social_inbox."""
    session_token = peer.peer.session_token_for(sidecar_did)
    vp = peer.peer.presentation_for(sidecar_did, nonce="conformance-routing-2-0123456789ab")
    marker = "do-not-leak-to-inbox-xyz-7c9d"
    body = _message_send(_free_form_envelope(marker), message_id="msg-routing-not-in-inbox")
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
            f"setup failure: unsolicited inbound returned {resp.status_code}, expected 202; "
            f"{resp.text[:200]}"
        )

    # Pull the inbox; assert the marker text is absent.
    inbox_resp = await http.post(f"{sidecar_url}/mcp/social_inbox", json={"limit": 100})
    if inbox_resp.status_code == 404:
        pytest.fail("Sidecar does not expose social_inbox over HTTP — required by RFC-0007.")
    inbox_resp.raise_for_status()
    items = inbox_resp.json().get("items", [])
    serialized = json.dumps(items)
    assert marker not in serialized, (
        "RFC-0006 §Cost guarantee fail: unsolicited inbound surfaced in social_inbox. "
        f"Marker {marker!r} found in items."
    )
