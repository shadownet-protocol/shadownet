"""End-to-end Sarah → Lukas slice of the Birthday flow.

Mirrors the wire artifacts in `shadownet-specs/examples/birthday-flow.md` using
only in-process components: deterministic keypairs, in-memory caches, no HTTP.

The test exercises:
- did:key derivation for Sarah and Lukas
- SCA issues an L2 credential about Sarah, plus a freshness proof
- Sarah mints a session token and a VP bound to Lukas's audience
- Lukas's Sidecar runs `verify_handshake`, produces a HandshakeContext
- The envelope inside the A2A message is a valid ShadownetEnvelope
"""

from __future__ import annotations

import time

from shadownet.a2a.envelope import (
    ENVELOPE_PART_TYPE,
    ShadownetEnvelope,
    decode_envelope_part,
    envelope_part,
)
from shadownet.a2a.server import verify_handshake
from shadownet.a2a.session import mint_session_token
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.key import derive_did_key
from shadownet.did.resolver import Resolver
from shadownet.sca.predicate import LevelLeaf
from shadownet.trust import TrustStore
from shadownet.vc.credential import issue_credential, new_credential
from shadownet.vc.freshness import mint_freshness_proof
from shadownet.vc.presentation import mint_presentation

SCA_DID = "did:web:sca.shadownet.example"  # represented by an Ed25519 key in-process
INTERACTION = "urn:shadownet:int:scheduling.v0-draft"


def _propose_payload() -> dict:
    return {
        "kind": "propose",
        "title": "Sarah's birthday outing",
        "constraints": {
            "when": "Sun 2026-05-10 afternoon",
            "where": {"city": "Berlin", "type": "park"},
            "weather": "sunny",
        },
        "respondBy": "2026-05-04T18:00:00Z",
    }


async def test_sarah_to_lukas_handshake_and_envelope() -> None:
    sca_kp = Ed25519KeyPair.generate()
    sarah_kp = Ed25519KeyPair.generate()
    sarah_did = derive_did_key(sarah_kp.public_bytes)
    lukas_kp = Ed25519KeyPair.generate()
    lukas_did = derive_did_key(lukas_kp.public_bytes)

    # In the demo SCA_DID is a did:web — for an in-process test we substitute
    # the issuer DID with a did:key bound to sca_kp so resolution is local.
    sca_did = derive_did_key(sca_kp.public_bytes)

    issued_at = int(time.time()) - 3 * 24 * 3600  # 3 days ago
    cred = new_credential(
        issuer=sca_did,
        subject=sarah_did,
        level="urn:shadownet:level:L2",
        subject_type="person",
        issued_at=issued_at,
        jti="urn:uuid:5b7c1c4a-0000-0000-0000-000000000001",
    )
    cred_jwt = issue_credential(issuer_key=sca_kp, issuer_kid=sca_did, credential=cred)

    freshness_jwt = mint_freshness_proof(
        issuer_key=sca_kp,
        issuer_did=sca_did,
        issuer_kid=sca_did,
        credential_jti=cred.jti,
    )

    session_jwt = mint_session_token(
        holder_key=sarah_kp,
        holder_did=sarah_did,
        audience_did=lukas_did,
    )

    vp_jwt = mint_presentation(
        holder_key=sarah_kp,
        holder_did=sarah_did,
        audience_did=lukas_did,
        credentials=[cred_jwt],
        freshness_proofs=[freshness_jwt],
    )

    # Lukas's trust store accepts SCA at L1+L2.
    trust_store = TrustStore.from_pairs(
        [(sca_did, ["urn:shadownet:level:L1", "urn:shadownet:level:L2"])]
    )

    headers = {
        "Authorization": f"Bearer {session_jwt}",
        "X-Shadownet-Presentation": vp_jwt,
    }
    ctx = await verify_handshake(
        headers,
        expected_audience=lukas_did,
        resolver=Resolver(),
        trust_store=trust_store,
        required_predicate=LevelLeaf(level="urn:shadownet:level:L2"),
    )
    assert ctx.caller_did == sarah_did
    assert ctx.presentation is not None
    assert len(ctx.presentation.credentials) == 1
    accepted = ctx.presentation.credentials[0]
    assert accepted.iss == sca_did
    assert accepted.level == "urn:shadownet:level:L2"

    # Build and decode the envelope as it would appear inside the A2A message.
    envelope = ShadownetEnvelope(
        **{
            "shadownet:v": "0.1",
            "intentId": "urn:uuid:int-001",
            "interaction": INTERACTION,
            "payload": _propose_payload(),
        }
    )
    part = envelope_part(envelope)
    assert part["type"] == ENVELOPE_PART_TYPE
    decoded = decode_envelope_part(part)
    assert decoded.payload["kind"] == "propose"
    assert decoded.intent_id == "urn:uuid:int-001"
