# RFC-0004 §GET /status/<list-id>

"""SCA MUST publish BitstringStatusList credentials at the URLs named in credentialStatus."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from shadownet.crypto.jwt import decode_unverified_claims
from shadownet.sca.client import SCAClient

pytestmark = pytest.mark.class_("sca")


@pytest.mark.network
@pytest.mark.rfc("0004", section="StatusList", requirement="reachable_for_issued_credential")
async def test_status_list_credential_reachable(sca_client: SCAClient, http: httpx.AsyncClient):
    """An issued credential's credentialStatus.statusListCredential MUST be fetchable."""
    session = await sca_client.start_proof(level="urn:shadownet:level:L1")
    _, credential = await sca_client.request_issuance(
        session_id=session.session_id, level="urn:shadownet:level:L1", subject_type="person"
    )
    if credential.vc.credential_status is None:
        pytest.skip("issued credential has no credentialStatus; status-list tests N/A")
    url = credential.vc.credential_status.status_list_credential
    resp = await http.get(url)
    assert resp.status_code == 200, f"GET {url} -> {resp.status_code}"


@pytest.mark.network
@pytest.mark.rfc("0004", section="StatusList", requirement="is_a_vc_jwt")
async def test_status_list_is_a_signed_vc_jwt(sca_client: SCAClient, http: httpx.AsyncClient):
    session = await sca_client.start_proof(level="urn:shadownet:level:L1")
    _, credential = await sca_client.request_issuance(
        session_id=session.session_id, level="urn:shadownet:level:L1", subject_type="person"
    )
    if credential.vc.credential_status is None:
        pytest.skip("issued credential has no credentialStatus; status-list tests N/A")
    resp = await http.get(credential.vc.credential_status.status_list_credential)
    body = resp.text.strip()
    # Body may be a raw JWT (compact serialization) or wrapped as JSON.
    try:
        wrapped = json.loads(body)
        token = wrapped if isinstance(wrapped, str) else wrapped.get("credential", body)
    except json.JSONDecodeError:
        token = body
    parts = token.split(".")
    assert len(parts) == 3, "status list response must be a compact JWS"
    header_json = base64.urlsafe_b64decode(parts[0] + "==").decode()
    assert json.loads(header_json).get("alg") == "EdDSA"
    claims = decode_unverified_claims(token)
    assert claims.get("shadownet:v") == "0.1"
    encoded_list = claims.get("vc", {}).get("credentialSubject", {}).get("encodedList")
    assert isinstance(encoded_list, str) and encoded_list, (
        "BitstringStatusList must carry a non-empty encodedList"
    )


@pytest.mark.network
@pytest.mark.should
@pytest.mark.rfc("0004", section="StatusList", requirement="cache_control_max_age")
async def test_status_list_has_cache_control(sca_client: SCAClient, http: httpx.AsyncClient):
    """SHOULD cache for 5 min by default — Cache-Control header should reflect that."""
    session = await sca_client.start_proof(level="urn:shadownet:level:L1")
    _, credential = await sca_client.request_issuance(
        session_id=session.session_id, level="urn:shadownet:level:L1", subject_type="person"
    )
    if credential.vc.credential_status is None:
        pytest.skip("issued credential has no credentialStatus; status-list tests N/A")
    resp = await http.get(credential.vc.credential_status.status_list_credential)
    assert "cache-control" in {h.lower() for h in resp.headers}, (
        "status list SHOULD set Cache-Control"
    )
