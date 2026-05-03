# RFC-0004 §POST /freshness

"""SCA MUST mint a freshness proof for a credential it issued, only to its holder."""

from __future__ import annotations

import pytest
from shadownet.sca.client import SCAClient
from shadownet.sca.errors import SCAError, SCAHTTPError

pytestmark = pytest.mark.class_("sca")

L1 = "urn:shadownet:level:L1"


@pytest.mark.network
@pytest.mark.rfc("0004", section="Freshness", requirement="happy_path")
async def test_freshness_for_issued_credential(sca_client: SCAClient):
    session = await sca_client.start_proof(level=L1)
    _, credential = await sca_client.request_issuance(
        session_id=session.session_id, level=L1, subject_type="person"
    )
    proof_token, proof = await sca_client.request_freshness(credential_jti=credential.jti)
    assert proof_token, "freshness proof JWT must be non-empty"
    assert proof.sub == credential.jti
    assert proof.iss == credential.iss
    assert proof.exp > proof.iat
    # RFC-0003 §Lifetimes: freshness proof MUST be ≤ 24h.
    assert proof.exp - proof.iat <= 24 * 3600, (
        f"freshness proof TTL {proof.exp - proof.iat}s exceeds 24h"
    )


@pytest.mark.network
@pytest.mark.rfc("0004", section="Freshness", requirement="unknown_jti_rejected")
async def test_unknown_jti_rejected(sca_client: SCAClient):
    with pytest.raises((SCAError, SCAHTTPError)):
        await sca_client.request_freshness(
            credential_jti="urn:uuid:00000000-aaaa-bbbb-cccc-deadbeefdead"
        )
