# RFC-0006 §Invitation envelopes

"""Invitation-hint surfacing conformance.

An envelope from an unknown sender carrying ``hints.purpose = "invitation"``
MUST land in quarantine with the purpose visible, and the introducer hint
MUST be surfaced as a UI hint but MUST NOT bypass routing — verifying that
the introducer didn't skip quarantine is what makes the cost guarantee
robust against the "vouching" attack the RFC explicitly warns about.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = [pytest.mark.class_("sidecar"), pytest.mark.quarantine]


def _message_send(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "message:send",
        "params": {
            "message": {
                "role": "user",
                "messageId": "msg-invitation-hint",
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


@pytest.mark.network
@pytest.mark.rfc("0006", section="invitation-envelopes", requirement="hints_surface_in_quarantine")
async def test_invitation_hints_surface_in_quarantine(sidecar_url, http, sidecar_did, peer) -> None:
    """RFC-0006 §Invitation envelopes: purpose + introducer fields surface in quarantine."""
    session_token = peer.peer.session_token_for(sidecar_did)
    vp = peer.peer.presentation_for(sidecar_did, nonce="conformance-inv-1-0123456789ab")
    text = "Hi, I'm Alex. I'd like to coordinate on Project Foo."
    introducer_did = "did:key:z6MkBobIntroducerForConformanceTest"
    body = _message_send(
        {
            "shadownet:v": "0.1",
            "intentId": "urn:uuid:00000000-0000-4000-b000-000000000001",
            "payload": {
                "text": text,
                "hints": {
                    "purpose": "invitation",
                    "proposed_collaboration": "Project Foo",
                    "introducer_contact": introducer_did,
                },
            },
        }
    )
    resp = await http.post(
        f"{sidecar_url}/a2a/message:send",
        headers={
            "Authorization": f"Bearer {session_token}",
            "X-Shadownet-Presentation": vp,
        },
        json=body,
    )
    assert resp.status_code == 202

    ql = await http.post(
        f"{sidecar_url}/mcp/social_quarantine_list",
        json={"limit": 100},
    )
    if ql.status_code == 404:
        pytest.fail("Sidecar does not expose social_quarantine_list — required by RFC-0007.")
    ql.raise_for_status()
    items = ql.json().get("items", [])
    item = next((it for it in items if it.get("summary") == text), None)
    assert item is not None, "invitation envelope did not surface in quarantine"
    assert item.get("purpose") == "invitation", (
        f"hints.purpose MUST surface as item.purpose; got {item.get('purpose')!r}"
    )
    assert item.get("introducer") == introducer_did, (
        f"hints.introducer_contact MUST surface as item.introducer; got {item.get('introducer')!r}"
    )


@pytest.mark.network
@pytest.mark.rfc(
    "0006",
    section="invitation-envelopes",
    requirement="introducer_does_not_bypass_quarantine",
)
async def test_introducer_does_not_bypass_quarantine(sidecar_url, http, sidecar_did, peer) -> None:
    """RFC-0006: receivers MUST NOT treat introducer_contact as a quarantine bypass.

    Even if the introducer DID is well-formed, the sender MUST land in
    quarantine — vouching is a UI hint at v0.1, never an authentication
    shortcut.
    """
    session_token = peer.peer.session_token_for(sidecar_did)
    vp = peer.peer.presentation_for(sidecar_did, nonce="conformance-inv-2-0123456789ab")
    body = _message_send(
        {
            "shadownet:v": "0.1",
            "intentId": "urn:uuid:00000000-0000-4000-b000-000000000002",
            "payload": {
                "text": "introducer-bypass-attempt",
                "hints": {
                    "purpose": "invitation",
                    "introducer_contact": "did:key:z6MkWellKnownIntroducerThatShouldNotBypass",
                },
            },
        }
    )
    resp = await http.post(
        f"{sidecar_url}/a2a/message:send",
        headers={
            "Authorization": f"Bearer {session_token}",
            "X-Shadownet-Presentation": vp,
        },
        json=body,
    )
    assert resp.status_code == 202, (
        f"introducer hint MUST NOT bypass quarantine; got {resp.status_code} "
        "(any non-202 means the routing decision changed based on introducer_contact)"
    )
