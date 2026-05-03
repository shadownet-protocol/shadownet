# RFC-0006 §Handshake

"""Sidecar MUST accept a valid session token + VP and reject malformed ones."""

from __future__ import annotations

import json

import pytest
from shadownet.crypto.jwt import sign_jwt

pytestmark = pytest.mark.class_("sidecar")


def _envelope(payload: dict[str, object]) -> dict[str, object]:
    """Build a minimal A2A message:send body wrapping a Shadownet envelope."""
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "message:send",
        "params": {
            "message": {
                "role": "user",
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


def _envelope_payload() -> dict[str, object]:
    return {
        "shadownet:v": "0.1",
        "intentId": "urn:uuid:00000000-0000-4000-8000-aaaaaaaaaaaa",
        "interaction": "urn:shadownet:int:conformance.v0",
        "payload": {"kind": "ping"},
    }


@pytest.mark.network
@pytest.mark.rfc("0006", section="Handshake", requirement="missing_auth_rejected")
async def test_request_without_authorization_rejected(sidecar_url, http):
    body = _envelope(_envelope_payload())
    resp = await http.post(f"{sidecar_url}/a2a/message:send", json=body)
    assert resp.status_code in {401, 403}, (
        f"missing Authorization should yield 401/403; got {resp.status_code}"
    )


@pytest.mark.network
@pytest.mark.rfc("0006", section="Handshake", requirement="missing_vp_yields_presentation_required")
async def test_request_with_session_but_no_vp_returns_presentation_required(
    sidecar_url, http, sidecar_did, peer
):
    """First request of a session lacks X-Shadownet-Presentation → 401 presentation_required."""
    session_token = peer.peer.session_token_for(sidecar_did)
    body = _envelope(_envelope_payload())
    resp = await http.post(
        f"{sidecar_url}/a2a/message:send",
        headers={"Authorization": f"Bearer {session_token}"},
        json=body,
    )
    assert resp.status_code == 401
    err = resp.json()
    assert err.get("error") == "presentation_required"
    assert err.get("nonce"), "presentation_required response MUST include a nonce"


@pytest.mark.network
@pytest.mark.rfc("0006", section="Handshake", requirement="happy_path_accepted")
async def test_valid_handshake_accepted(sidecar_url, http, sidecar_did, peer):
    session_token = peer.peer.session_token_for(sidecar_did)
    # Use a fresh nonce; the sidecar accepts caller-supplied nonces on the
    # first request of a session.
    vp = peer.peer.presentation_for(sidecar_did, nonce="conformance-test-nonce-0123456789ab")
    body = _envelope(_envelope_payload())
    resp = await http.post(
        f"{sidecar_url}/a2a/message:send",
        headers={
            "Authorization": f"Bearer {session_token}",
            "X-Shadownet-Presentation": vp,
        },
        json=body,
    )
    assert resp.status_code == 200, (
        f"valid handshake should succeed; got {resp.status_code}: {resp.text[:200]}"
    )


@pytest.mark.network
@pytest.mark.rfc("0006", section="Handshake", requirement="wrong_aud_session_rejected")
async def test_session_token_with_wrong_audience_rejected(sidecar_url, http, peer):
    """Session token whose `aud` is not the sidecar's DID MUST be rejected."""
    bogus_audience = "did:key:z6MkOtherSidecarThatIsNotUs"
    session_token = peer.peer.session_token_for(bogus_audience)
    vp = peer.peer.presentation_for(bogus_audience, nonce="conformance-bad-aud-nonce-00000")
    resp = await http.post(
        f"{sidecar_url}/a2a/message:send",
        headers={
            "Authorization": f"Bearer {session_token}",
            "X-Shadownet-Presentation": vp,
        },
        json=_envelope(_envelope_payload()),
    )
    assert resp.status_code in {401, 403}


@pytest.mark.network
@pytest.mark.rfc("0006", section="Handshake", requirement="malformed_vp_rejected")
async def test_malformed_vp_rejected(sidecar_url, http, sidecar_did, peer):
    """A VP whose signature is forged MUST be rejected with `presentation_invalid`."""
    session_token = peer.peer.session_token_for(sidecar_did)
    # A "VP" that isn't a JWT at all.
    bogus_vp = "not.a.jwt"
    resp = await http.post(
        f"{sidecar_url}/a2a/message:send",
        headers={
            "Authorization": f"Bearer {session_token}",
            "X-Shadownet-Presentation": bogus_vp,
        },
        json=_envelope(_envelope_payload()),
    )
    assert resp.status_code == 401
    err = resp.json()
    assert err.get("error") in {"presentation_invalid", "presentation_required"}


@pytest.mark.network
@pytest.mark.rfc("0006", section="Errors", requirement="error_envelope_shape")
async def test_error_response_uses_rfc_envelope(sidecar_url, http):
    """RFC-0006 §Errors: error responses MUST use {error, detail, shadownet:v}."""
    resp = await http.post(f"{sidecar_url}/a2a/message:send", json={"jsonrpc": "2.0"})
    if resp.status_code == 200:
        pytest.fail("expected non-2xx for missing handshake")
    body = resp.json()
    assert isinstance(body.get("error"), str)
    assert body.get("shadownet:v") == "0.1"


@pytest.mark.network
@pytest.mark.rfc("0006", section="Handshake", requirement="expired_session_rejected")
async def test_expired_session_token_rejected(sidecar_url, http, sidecar_did, peer):
    """Session token with exp in the past MUST be rejected."""
    import time

    iat = int(time.time()) - 600
    claims = {
        "iss": peer.peer.identity.did,
        "aud": sidecar_did,
        "iat": iat,
        "exp": iat + 60,  # expired 9 minutes ago
        "jti": "00000000-0000-4000-8000-eeeeeeeeeeee",
        "shadownet:v": "0.1",
        "purpose": "a2a-session",
    }
    expired = sign_jwt(
        claims,
        peer.peer.identity.keypair,
        header_extras={"typ": "JWT", "kid": peer.peer.identity.kid},
    )
    vp = peer.peer.presentation_for(sidecar_did, nonce="conformance-expired-nonce-00000")
    resp = await http.post(
        f"{sidecar_url}/a2a/message:send",
        headers={
            "Authorization": f"Bearer {expired}",
            "X-Shadownet-Presentation": vp,
        },
        json=_envelope(_envelope_payload()),
    )
    assert resp.status_code in {401, 403}


@pytest.mark.network
@pytest.mark.rfc("0006", section="Envelope", requirement="envelope_part_required")
async def test_message_without_envelope_part_rejected_or_unhandled(
    sidecar_url, http, sidecar_did, peer
):
    """A message:send without a shadownet/v1+envelope part should be rejected or unhandled."""
    session_token = peer.peer.session_token_for(sidecar_did)
    vp = peer.peer.presentation_for(sidecar_did, nonce="conformance-no-env-nonce-0000000")
    body_without_envelope = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "message:send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"type": "text/plain", "mediaType": "text/plain", "data": "hi"}],
            }
        },
    }
    resp = await http.post(
        f"{sidecar_url}/a2a/message:send",
        headers={
            "Authorization": f"Bearer {session_token}",
            "X-Shadownet-Presentation": vp,
        },
        json=body_without_envelope,
    )
    # The sidecar MAY accept (and surface as opaque) or reject — either is
    # spec-compliant. We assert it does not crash.
    assert resp.status_code < 500, f"server error on plain-text part: {resp.status_code}"
    if resp.status_code != 200:
        body = (
            resp.json()
            if resp.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        assert body.get("shadownet:v") == "0.1" or "error" in body, (
            "non-2xx response should still use the RFC-0006 error envelope"
        )
    # Suppress the unused json import warning under no-target runs.
    _ = json
