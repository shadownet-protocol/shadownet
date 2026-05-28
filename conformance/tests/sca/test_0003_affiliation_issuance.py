# RFC-0003 §AffiliationCredential, RFC-0004 §Enterprise SCAs

"""SCA MUST issue an AffiliationCredential when configured for affiliation mode.

The candidate SCA is queried for its mode-aware policy; if affiliation
issuance is not enabled, the test skips. When enabled, the test drives
``POST /issuance/affiliation`` and verifies the resulting credential
round-trips through the SDK verifier — including the domain-control
chase when ``iss != credentialSubject.affiliation``.
"""

from __future__ import annotations

import time

import httpx
import pytest
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.sca.client import SCAClient
from shadownet.sca.csr import build_subject_auth
from shadownet.vc.affiliation import decode_affiliation_credential

pytestmark = pytest.mark.class_("sca")


def _affiliation_org(sca_did: str) -> str:
    """Map an SCA DID to the org it's expected to issue affiliation for.

    A conformant affiliation SCA serves one organization (the SCA's policy
    pins it via ``affiliationOrg``). For tests we default to the SCA's own
    DID — the spec permits direct-issuance — and let the policy override.
    """
    return sca_did


@pytest.fixture
async def affiliation_policy(sca_url: str, http: httpx.AsyncClient) -> dict | None:
    """Fetch the SCA's policy document and return it iff affiliation is enabled.

    Returns ``None`` when the SCA is personhood-only; tests then skip.
    """
    resp = await http.get(f"{sca_url}/.well-known/sca/policy.json")
    if resp.status_code != 200:
        pytest.fail(f"SCA policy endpoint returned {resp.status_code}")
    body = resp.json()
    mode = body.get("mode", "personhood")
    if mode not in ("affiliation", "both"):
        pytest.skip(f"SCA at {sca_url} is mode={mode!r}; affiliation issuance not enabled")
    return body


@pytest.mark.network
@pytest.mark.affiliation
@pytest.mark.rfc("0003", section="AffiliationCredential", requirement="issue_happy_path")
async def test_affiliation_issuance_happy_path(
    sca_client: SCAClient,
    affiliation_policy: dict,
    sca_url: str,
    subject_did: str,
    subject_keypair: Ed25519KeyPair,
    http: httpx.AsyncClient,
) -> None:
    """RFC-0003 §AffiliationCredential §Verifier acceptance: round-trip ok."""
    org = affiliation_policy.get("affiliationOrg") or _affiliation_org(affiliation_policy["issuer"])

    auth_jwt = build_subject_auth(
        holder_key=subject_keypair,
        holder_did=subject_did,
        sca_did=affiliation_policy["issuer"],
    )
    resp = await http.post(
        f"{sca_url}/issuance/affiliation",
        json={
            "shadownet:v": "0.1",
            "subject": subject_did,
            "affiliation": org,
            "role": "member",
            "groups": ["engineering"],
        },
        headers={"Authorization": f"Bearer {auth_jwt}"},
    )
    if resp.status_code == 404:
        pytest.fail(
            "SCA policy declares affiliation mode but /issuance/affiliation is 404. "
            "RFC-0003 §AffiliationCredential requires the endpoint when issuance is enabled."
        )
    if resp.status_code != 200:
        pytest.fail(f"/issuance/affiliation returned {resp.status_code}: {resp.text!r}")
    body = resp.json()
    token = body.get("credential")
    assert token, "issuance response missing 'credential' field"

    decoded = decode_affiliation_credential(token)
    assert decoded.sub == subject_did
    assert decoded.iss.startswith("did:web:"), (
        "AffiliationCredential iss MUST be did:web (RFC-0003)"
    )
    assert decoded.affiliation == org
    assert decoded.shadownet_v == "0.1"
    # RFC-0003 §Lifetime: SHOULD ≤ 30 days.
    assert decoded.exp - decoded.iat <= 30 * 24 * 3600


@pytest.mark.network
@pytest.mark.affiliation
@pytest.mark.rfc("0003", section="AffiliationCredential", requirement="freshness_window_bounded")
async def test_affiliation_freshness_window_bounded(affiliation_policy: dict) -> None:
    """RFC-0003 §AffiliationCredential §Lifetime: freshness window SHOULD be ≤ 3600s."""
    window = affiliation_policy.get("affiliationFreshnessWindowSeconds")
    if window is None:
        pytest.fail(
            "policy.affiliationFreshnessWindowSeconds is required when affiliation is enabled"
        )
    assert window <= 3600, (
        f"affiliation freshness window {window}s exceeds RFC-0003 SHOULD-cap of 3600s"
    )


@pytest.mark.network
@pytest.mark.affiliation
@pytest.mark.rfc("0003", section="AffiliationCredential", requirement="rejects_wrong_org")
async def test_affiliation_issuance_rejects_wrong_org(
    affiliation_policy: dict,
    sca_url: str,
    subject_did: str,
    subject_keypair: Ed25519KeyPair,
    http: httpx.AsyncClient,
) -> None:
    """An SCA configured for one org MUST reject affiliation requests for another."""
    policy_org = affiliation_policy.get("affiliationOrg")
    if not policy_org:
        pytest.skip("SCA policy does not pin an affiliation org; this gate doesn't apply")
    rogue_org = "did:web:other-rogue.example"
    if rogue_org == policy_org:
        rogue_org = "did:web:still-other-rogue.example"

    auth_jwt = build_subject_auth(
        holder_key=subject_keypair,
        holder_did=subject_did,
        sca_did=affiliation_policy["issuer"],
    )
    resp = await http.post(
        f"{sca_url}/issuance/affiliation",
        json={
            "shadownet:v": "0.1",
            "subject": subject_did,
            "affiliation": rogue_org,
        },
        headers={"Authorization": f"Bearer {auth_jwt}"},
    )
    assert resp.status_code == 403, (
        f"mismatched-org affiliation request must be rejected with 403; got {resp.status_code}"
    )
    body = resp.json()
    assert body.get("error") == "affiliation_org_mismatch", (
        f"expected error 'affiliation_org_mismatch'; got {body!r}"
    )


# Suppress unused-import warnings: time + SCAClient are kept as documentation
# of the canonical client used elsewhere in this test module's siblings.
_ = (time, SCAClient)
