# RFC-0004 §POST /proof/status

"""SCA MUST report session state via /proof/status."""

from __future__ import annotations

import pytest
from shadownet.sca.client import SCAClient

pytestmark = pytest.mark.class_("sca")


@pytest.mark.network
@pytest.mark.rfc("0004", section="ProofStatus", requirement="reports_state")
async def test_proof_status_reports_state(sca_client: SCAClient):
    session = await sca_client.start_proof(level="urn:shadownet:level:L1")
    status = await sca_client.poll_proof(session.session_id)
    assert status.shadownet_v == "0.1"
    assert status.session_id == session.session_id
    assert status.status in {"pending", "ready"}, (
        f"unexpected status {status.status!r}; instant-approval methods should be 'ready'"
    )


@pytest.mark.network
@pytest.mark.rfc("0004", section="ProofStatus", requirement="instant_approval_ready")
async def test_proof_status_ready_after_instant_approval(sca_client: SCAClient):
    """For an instant-approval proof method, the session MUST become 'ready' immediately."""
    session = await sca_client.start_proof(level="urn:shadownet:level:L1")
    status = await sca_client.poll_proof(session.session_id)
    assert status.status == "ready", (
        f"instant-approval method left session in state {status.status!r}; "
        "if your SCA's --proof-method is not actually instant-approval, "
        "configure a different one for conformance runs"
    )


@pytest.mark.network
@pytest.mark.rfc("0004", section="ProofStatus", requirement="unknown_session_rejected")
async def test_unknown_session_id_rejected(sca_client: SCAClient):
    from shadownet.sca.errors import SCAError, SCAHTTPError

    with pytest.raises((SCAError, SCAHTTPError)):
        await sca_client.poll_proof("ses-totally-bogus-does-not-exist")
