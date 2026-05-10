from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest
from shadownet.crypto.jwt import decode_unverified_claims
from shadownet.webhook.verify import build_webhook_headers

from shadownet_conformance.peer import spawn_peer

if TYPE_CHECKING:
    from shadownet_conformance.peer import PeerHandle


@pytest.fixture(scope="module")
def peer():
    handle: PeerHandle = spawn_peer()
    try:
        yield handle
    finally:
        handle.stop()


@pytest.fixture(autouse=True)
def _reset_peer(peer: PeerHandle):
    peer.peer.reset()


async def test_agent_card_serves_did_and_public_key(peer: PeerHandle):
    async with httpx.AsyncClient() as http:
        resp = await http.get(peer.agent_card_url)
    assert resp.status_code == 200
    card = resp.json()
    assert card["did"] == peer.peer.identity.did
    assert card["publicKey"]["kty"] == "OKP"
    assert card["publicKey"]["crv"] == "Ed25519"
    assert card["shadownet:v"] == "0.1"


async def test_a2a_records_request_and_returns_scripted_response(peer: PeerHandle):
    peer.peer.script_a2a_response(
        status=200,
        body={"jsonrpc": "2.0", "id": "abc", "result": {"taskId": "tsk-1"}},
    )
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"{peer.a2a_url}/message:send",
            headers={"X-Test": "yes"},
            json={"jsonrpc": "2.0", "id": "abc", "method": "message:send", "params": {}},
        )
    assert resp.status_code == 200
    assert resp.json() == {"jsonrpc": "2.0", "id": "abc", "result": {"taskId": "tsk-1"}}

    received = peer.peer.last_a2a_request()
    assert received is not None
    assert received.method == "message:send"
    assert received.headers["x-test"] == "yes"
    assert received.json_body is not None
    assert received.json_body["method"] == "message:send"


async def test_webhook_receiver_verifies_hmac(peer: PeerHandle):
    body = json.dumps(
        {
            "shadownet:v": "0.1",
            "event": "inbox.message",
            "occurredAt": 1759200000,
            "data": {
                "intentId": "urn:uuid:int-001",
                "contactId": "ctc-x",
                "interaction": "urn:shadownet:int:test.v0",
                "messageId": "msg-1",
            },
        }
    ).encode()
    headers = build_webhook_headers(
        body=body, secret=peer.peer.identity.webhook_secret, sidecar_id="sc-test"
    )
    async with httpx.AsyncClient() as http:
        resp = await http.post(peer.webhook_url, headers=headers, content=body)
    assert resp.status_code == 200
    delivered = peer.peer.delivered_webhooks()
    assert len(delivered) == 1
    assert delivered[0].verification_error is None
    assert delivered[0].event is not None
    assert delivered[0].event.event == "inbox.message"


async def test_webhook_receiver_rejects_bad_hmac(peer: PeerHandle):
    body = b'{"shadownet:v":"0.1","event":"inbox.message","occurredAt":1759200000,"data":{}}'
    bad_headers = build_webhook_headers(
        body=body, secret="WRONG-SECRET-which-is-also-32-bytes-long", sidecar_id="sc"
    )
    async with httpx.AsyncClient() as http:
        resp = await http.post(peer.webhook_url, headers=bad_headers, content=body)
    assert resp.status_code == 400
    delivered = peer.peer.delivered_webhooks()
    assert len(delivered) == 1
    assert delivered[0].verification_error is not None


async def test_session_token_for_round_trips_through_audience(peer: PeerHandle):
    audience_did = "did:key:z6MkAudienceForTest"
    token = peer.peer.session_token_for(audience_did)
    claims = decode_unverified_claims(token)
    assert claims["iss"] == peer.peer.identity.did
    assert claims["aud"] == audience_did
    assert claims["purpose"] == "a2a-session"


async def test_presentation_for_includes_credential_and_freshness(peer: PeerHandle):
    audience_did = "did:key:z6MkAudienceForTest"
    vp = peer.peer.presentation_for(audience_did, nonce="abc123abc123abc123abc123abc123ab")
    claims = decode_unverified_claims(vp)
    assert claims["iss"] == peer.peer.identity.did
    assert claims["aud"] == audience_did
    assert claims["nonce"] == "abc123abc123abc123abc123abc123ab"
    inner = claims["vp"]["verifiableCredential"]
    assert len(inner) == 2
    cred_claims = decode_unverified_claims(inner[0])
    assert cred_claims["sub"] == peer.peer.identity.did
    assert cred_claims["vc"]["credentialSubject"]["level"] == "urn:shadownet:level:L2"
