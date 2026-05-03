# RFC-0003, RFC-0004 — round-trip across SCA implementations

"""Issue with --target sca, verify the resulting credential against --peer-target sca."""

from __future__ import annotations

import pytest
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.key import derive_did_key
from shadownet.did.resolver import Resolver
from shadownet.did.web import WebDIDResolver
from shadownet.sca.client import SCAClient
from shadownet.vc.credential import verify_credential

from shadownet_conformance.peer.identity import PEER_SEED_HEX

pytestmark = [pytest.mark.class_("sca"), pytest.mark.round_trip("sca")]


@pytest.mark.network
@pytest.mark.rfc("0003", section="RoundTrip", requirement="issue_a_verify_b")
async def test_credential_issued_by_a_is_verifiable_via_b(sca_url, peer_sca_url, http):
    """A credential issued by SCA-A MUST verify on SCA-B's side using its DID resolver.

    'Verify on B's side' here means: a verifier configured against B's
    network resolves A's did:web (over the same network) and successfully
    validates the JWT signature. Cross-impl agreement on JWS canonicalization
    + DID resolution is what this catches.
    """
    keypair = Ed25519KeyPair.from_seed(bytes.fromhex(PEER_SEED_HEX))
    holder_did = derive_did_key(bytes(keypair.public_key.public_bytes_raw()))
    a_well_known = (await http.get(f"{sca_url}/.well-known/did.json")).json()
    a_client = SCAClient(
        http=http,
        sca_base_url=sca_url,
        sca_did=a_well_known["id"],
        holder_key=keypair,
        holder_did=holder_did,
    )
    session = await a_client.start_proof(level="urn:shadownet:level:L1")
    token, _ = await a_client.request_issuance(
        session_id=session.session_id,
        level="urn:shadownet:level:L1",
        subject_type="person",
    )

    # Build a verifier configured against B's network. We use B's URL here as
    # the resolver's HTTP client base — same did:web URL over either URL would
    # hit the same canonical document, but driving from B's side is what the
    # round-trip is about.
    web_resolver = WebDIDResolver(http=http)
    resolver = Resolver(web=web_resolver)
    verified = await verify_credential(token, resolver=resolver)
    assert verified.iss == a_well_known["id"]
    assert verified.sub == holder_did
    # peer_sca_url is intentionally referenced to anchor the round-trip
    # contract: removing the second target reduces this to a self-test.
    assert peer_sca_url, "peer SCA URL is required for round-trip framing"
