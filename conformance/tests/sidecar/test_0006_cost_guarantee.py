# RFC-0006 §Cost guarantee, RFC-0007 §Cost guarantee

"""Headline cost-guarantee assertion: unsolicited inbound MUST NOT trigger any
host-agent reasoning loop.

Drives an unsolicited A2A envelope from the in-process peer (a foreign
Shadow with no contact relationship to the SUT) and asserts:

1. The envelope is held in quarantine (visible via social_quarantine_list).
2. The quarantine item's ``summary`` field equals the sender-supplied
   ``payload.text`` byte-for-byte. Any divergence means the SUT did
   receiver-side LLM work to derive a summary — a cost-guarantee fail.
3. The envelope does NOT appear in ``social_inbox``.
4. No host-LLM-relevant event surfaced on ``social_inbox_wait`` for the
   unknown sender; the only quarantine-related event a conformant Sidecar
   may emit is ``quarantine.pending`` (which carries only sender-supplied
   fields).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

pytestmark = [pytest.mark.class_("sidecar"), pytest.mark.cost_guarantee]


def _free_form_envelope(text: str, hints: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"text": text}
    if hints is not None:
        payload["hints"] = hints
    return {
        "shadownet:v": "0.1",
        "intentId": f"urn:uuid:00000000-0000-4000-8000-{text.encode().hex()[:12].ljust(12, '0')}",
        "payload": payload,
    }


def _message_send(payload: dict[str, Any], message_id: str = "msg-cg") -> dict[str, Any]:
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


@pytest.mark.network
@pytest.mark.rfc("0006", section="cost-guarantee", requirement="summary_is_sender_supplied")
async def test_cost_guarantee_quarantine_summary_byte_equality(
    sidecar_url, http, sidecar_did, peer
) -> None:
    """The quarantine item's summary MUST equal the sender's payload.text byte-for-byte."""
    session_token = peer.peer.session_token_for(sidecar_did)
    vp = peer.peer.presentation_for(sidecar_did, nonce="conformance-cg-1-0123456789ab")
    text = "lorem ipsum — cost-guarantee marker 🦊"
    body = _message_send(_free_form_envelope(text, hints={"purpose": "invitation"}))

    resp = await http.post(
        f"{sidecar_url}/a2a/message:send",
        headers={
            "Authorization": f"Bearer {session_token}",
            "X-Shadownet-Presentation": vp,
        },
        json=body,
    )
    assert resp.status_code == 202, (
        f"setup: unsolicited inbound MUST be quarantined; got {resp.status_code}"
    )

    ql = await http.post(f"{sidecar_url}/mcp/social_quarantine_list", json={"limit": 100})
    if ql.status_code == 404:
        pytest.fail(
            "Sidecar does not expose social_quarantine_list — required by RFC-0007 "
            "enterprise + cost-containment amendment."
        )
    ql.raise_for_status()
    items = ql.json().get("items", [])
    match = next((it for it in items if it.get("summary") == text), None)
    assert match is not None, (
        "RFC-0006 §Cost guarantee: quarantine summary diverged from sender payload.text. "
        f"Sender supplied {text!r}; quarantine items: {json.dumps(items)[:400]}"
    )
    assert match.get("purpose") == "invitation"


@pytest.mark.network
@pytest.mark.rfc("0006", section="cost-guarantee", requirement="no_inbox_message_for_unknown")
async def test_cost_guarantee_no_inbox_message_event(sidecar_url, http, sidecar_did, peer) -> None:
    """Driving unsolicited inbound MUST NOT produce an inbox.message event."""
    session_token = peer.peer.session_token_for(sidecar_did)
    vp = peer.peer.presentation_for(sidecar_did, nonce="conformance-cg-2-0123456789ab")
    body = _message_send(_free_form_envelope("zero-llm-events-please"))

    # Start a short long-poll BEFORE we drive inbound so we capture any
    # event the Sidecar emits during the inbound processing window.
    async def poll_for_events() -> list[dict[str, Any]]:
        """Hit social_inbox_wait with a 2s timeout and collect the events."""
        resp = await http.post(
            f"{sidecar_url}/mcp/social_inbox_wait",
            json={"timeout_seconds": 2},
        )
        if resp.status_code == 404:
            pytest.fail("Sidecar does not expose social_inbox_wait — required by RFC-0007.")
        resp.raise_for_status()
        return list(resp.json().get("events", []))

    poll_task = asyncio.create_task(poll_for_events())
    # Small wait so the long-poll is registered before inbound arrives.
    await asyncio.sleep(0.1)

    send_resp = await http.post(
        f"{sidecar_url}/a2a/message:send",
        headers={
            "Authorization": f"Bearer {session_token}",
            "X-Shadownet-Presentation": vp,
        },
        json=body,
    )
    assert send_resp.status_code == 202, (
        f"setup: unsolicited inbound MUST be quarantined; got {send_resp.status_code}"
    )

    events = await poll_task

    for event in events:
        name = event.get("event")
        assert name != "inbox.message", (
            f"RFC-0006 §Cost guarantee FAIL: inbox.message event surfaced for an "
            f"unknown sender. Event payload: {event!r}"
        )
        # quarantine.pending is the only RFC-0007-recognized event that may
        # legitimately surface here.
        assert name in {"quarantine.pending"}, (
            f"unexpected event surfaced for unknown sender: {name!r}. "
            f"Only quarantine.pending is permitted under cost-guarantee."
        )


@pytest.mark.network
@pytest.mark.rfc("0006", section="cost-guarantee", requirement="quarantine_pending_has_no_body")
async def test_quarantine_pending_event_omits_body(sidecar_url, http, sidecar_did, peer) -> None:
    """quarantine.pending events MUST carry only quarantine metadata, not the body.

    RFC-0007 §Events: the event payload is {quarantineId, senderDid, purpose}.
    Surfacing the body in the event would defeat the cost guarantee — the
    host LLM would see the body without the user explicitly opening
    social_quarantine_list.
    """
    session_token = peer.peer.session_token_for(sidecar_did)
    vp = peer.peer.presentation_for(sidecar_did, nonce="conformance-cg-3-0123456789ab")
    sentinel = "must-not-leak-body-into-event-7c9d"
    body = _message_send(_free_form_envelope(sentinel))

    async def poll_for_events() -> list[dict[str, Any]]:
        resp = await http.post(
            f"{sidecar_url}/mcp/social_inbox_wait",
            json={"timeout_seconds": 2},
        )
        if resp.status_code == 404:
            pytest.fail("Sidecar does not expose social_inbox_wait — required by RFC-0007.")
        resp.raise_for_status()
        return list(resp.json().get("events", []))

    poll_task = asyncio.create_task(poll_for_events())
    await asyncio.sleep(0.1)

    send_resp = await http.post(
        f"{sidecar_url}/a2a/message:send",
        headers={
            "Authorization": f"Bearer {session_token}",
            "X-Shadownet-Presentation": vp,
        },
        json=body,
    )
    assert send_resp.status_code == 202

    events = await poll_task
    for event in events:
        if event.get("event") != "quarantine.pending":
            continue
        serialized = json.dumps(event.get("data") or {})
        assert sentinel not in serialized, (
            "RFC-0007 §Events: quarantine.pending event carried the body. "
            f"Sentinel {sentinel!r} found in event data: {serialized!r}"
        )
