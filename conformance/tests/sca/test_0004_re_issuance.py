# RFC-0004 §Re-issuance

"""SCA SHOULD revoke a prior credential when re-issuing for the same subject + level."""

from __future__ import annotations

import pytest
from shadownet.sca.client import SCAClient

pytestmark = pytest.mark.class_("sca")

L1 = "urn:shadownet:level:L1"


@pytest.mark.network
@pytest.mark.rfc("0004", section="ReIssuance", requirement="new_jti_each_issue")
async def test_re_issuance_yields_new_jti(sca_client: SCAClient):
    """Two consecutive issuances at the same level MUST produce distinct credentials (different jti)."""
    s1 = await sca_client.start_proof(level=L1)
    _, c1 = await sca_client.request_issuance(
        session_id=s1.session_id, level=L1, subject_type="person"
    )
    s2 = await sca_client.start_proof(level=L1)
    _, c2 = await sca_client.request_issuance(
        session_id=s2.session_id, level=L1, subject_type="person"
    )
    assert c1.jti != c2.jti, "re-issuance must produce a new jti"


@pytest.mark.network
@pytest.mark.should
@pytest.mark.rfc("0004", section="ReIssuance", requirement="freshness_still_obtainable")
async def test_freshness_for_new_credential_after_reissuance(sca_client: SCAClient):
    """After re-issuance, the new credential SHOULD have a working freshness endpoint."""
    s1 = await sca_client.start_proof(level=L1)
    await sca_client.request_issuance(session_id=s1.session_id, level=L1, subject_type="person")
    s2 = await sca_client.start_proof(level=L1)
    _, c2 = await sca_client.request_issuance(
        session_id=s2.session_id, level=L1, subject_type="person"
    )
    proof_token, _ = await sca_client.request_freshness(credential_jti=c2.jti)
    assert proof_token, "freshness for the new credential must be obtainable"
