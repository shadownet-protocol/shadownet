# RFC-0004 §POST /proof/start

"""SCA MUST open a proof session and return a session id + method-specific next step."""

from __future__ import annotations

import pytest
from shadownet.sca.client import SCAClient

pytestmark = pytest.mark.class_("sca")


@pytest.mark.network
@pytest.mark.rfc("0004", section="ProofStart", requirement="happy_path_returns_session")
async def test_proof_start_returns_session(sca_client: SCAClient, proof_method_uri: str):
    session = await sca_client.start_proof(level="urn:shadownet:level:L1")
    assert session.shadownet_v == "0.1"
    assert session.session_id, "sessionId must be non-empty"
    assert session.expires_at > 0
    assert session.method == proof_method_uri or session.method.endswith(proof_method_uri), (
        f"unexpected method: {session.method!r}; configured --proof-method {proof_method_uri!r}"
    )


@pytest.mark.network
@pytest.mark.rfc("0004", section="ProofStart", requirement="invalid_level_rejected")
async def test_proof_start_with_invalid_level(sca_client: SCAClient):
    """Levels not offered in policy MUST be rejected with `invalid_level`."""
    from shadownet.sca.errors import InvalidLevel, SCAHTTPError

    with pytest.raises((InvalidLevel, SCAHTTPError)) as exc_info:
        await sca_client.start_proof(level="urn:nonexistent:level:does-not-exist")
    # If the SDK normalized the error to InvalidLevel, fine; otherwise the
    # raw HTTP error must carry the documented status.
    if isinstance(exc_info.value, SCAHTTPError):
        assert "400" in str(exc_info.value), (
            f"unknown level should yield HTTP 400 (got {exc_info.value})"
        )
