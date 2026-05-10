# RFC-0004 §POST /issuance

"""SCA MUST issue a credential when given a CSR backed by a ready session."""

from __future__ import annotations

import pytest
from shadownet.sca.client import SCAClient
from shadownet.sca.errors import SCAError, SCAHTTPError

pytestmark = pytest.mark.class_("sca")

L1 = "urn:shadownet:level:L1"


@pytest.mark.network
@pytest.mark.rfc("0004", section="Issuance", requirement="happy_path")
async def test_issuance_happy_path(sca_client: SCAClient, subject_did: str, sca_url: str):
    session = await sca_client.start_proof(level=L1)
    token, credential = await sca_client.request_issuance(
        session_id=session.session_id, level=L1, subject_type="person"
    )
    assert token, "issued credential JWT must be non-empty"
    assert credential.sub == subject_did
    assert credential.iss.startswith("did:web:")
    assert credential.shadownet_v == "0.1"
    assert credential.vc.credential_subject.level == L1
    assert credential.vc.credential_subject.subject_type == "person"
    assert credential.exp > credential.iat


@pytest.mark.network
@pytest.mark.rfc("0004", section="Issuance", requirement="session_consumed")
async def test_session_cannot_be_reused(sca_client: SCAClient):
    """RFC-0004: once a session is consumed by a successful /issuance, it MUST be unusable."""
    session = await sca_client.start_proof(level=L1)
    await sca_client.request_issuance(
        session_id=session.session_id, level=L1, subject_type="person"
    )
    with pytest.raises((SCAError, SCAHTTPError)) as exc_info:
        await sca_client.request_issuance(
            session_id=session.session_id, level=L1, subject_type="person"
        )
    msg = str(exc_info.value).lower()
    assert "consumed" in msg or "410" in msg or "session" in msg, (
        f"reused session should be rejected; got {exc_info.value}"
    )


@pytest.mark.network
@pytest.mark.rfc("0004", section="Issuance", requirement="bogus_session_rejected")
async def test_bogus_session_id_rejected(sca_client: SCAClient):
    """Issuance against an unknown sessionId MUST fail."""
    with pytest.raises((SCAError, SCAHTTPError)):
        await sca_client.request_issuance(
            session_id="ses-does-not-exist",
            level=L1,
            subject_type="person",
        )
