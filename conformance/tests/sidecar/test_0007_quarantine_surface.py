# RFC-0007 §social_quarantine_list / §social_quarantine_review

"""Quarantine surface conformance: the Sidecar MUST expose the three
RFC-0007-amendment tools (`social_quarantine_list`, `social_quarantine_review`,
`social_set_contact_profile`) and the `social_grant` enum MUST accept the
`coordinate` verb.

Drives the candidate Sidecar over its MCP transport. End-to-end routing /
cost-guarantee tests (which need an unsolicited-inbound driver) ship in a
follow-up once a v0.2 reference Sidecar lands; this file ensures the read
side of the surface is consistent.
"""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.network
@pytest.mark.quarantine
@pytest.mark.rfc("0007", section="social_quarantine_list", requirement="surface_exists")
async def test_social_quarantine_list_is_exposed(sidecar_url: str, http: httpx.AsyncClient) -> None:
    """The Sidecar MUST expose social_quarantine_list under the MCP transport."""
    resp = await http.post(f"{sidecar_url}/mcp/social_quarantine_list", json={})
    if resp.status_code == 404:
        pytest.fail(
            "Sidecar does not expose social_quarantine_list — RFC-0007 "
            "enterprise + cost-containment amendment requires it (read scope)."
        )
    resp.raise_for_status()
    body = resp.json()
    assert "items" in body, "social_quarantine_list output MUST have an items array"
    assert isinstance(body["items"], list)


@pytest.mark.network
@pytest.mark.quarantine
@pytest.mark.rfc("0007", section="social_set_contact_profile", requirement="surface_exists")
async def test_social_set_contact_profile_is_exposed(
    sidecar_url: str, http: httpx.AsyncClient
) -> None:
    """The Sidecar MUST expose social_set_contact_profile (write scope)."""
    # We do not supply a valid contactId here; we only assert the endpoint
    # exists. A 4xx response is acceptable; 404 is not.
    resp = await http.post(
        f"{sidecar_url}/mcp/social_set_contact_profile",
        json={"contactId": "ctc_nonexistent", "profile": {"priority": "low"}},
    )
    if resp.status_code == 404:
        pytest.fail(
            "Sidecar does not expose social_set_contact_profile — RFC-0007 "
            "enterprise + cost-containment amendment requires it."
        )


@pytest.mark.network
@pytest.mark.quarantine
@pytest.mark.rfc("0007", section="social_grant", requirement="accepts_coordinate")
async def test_social_grant_accepts_coordinate_verb(
    sidecar_url: str, http: httpx.AsyncClient
) -> None:
    """social_grant MUST accept the new `coordinate` verb (the unknown contact id
    is OK — we only assert the verb is recognized at the schema level)."""
    resp = await http.post(
        f"{sidecar_url}/mcp/social_grant",
        json={"contactId": "ctc_nonexistent", "grant": "coordinate", "allowed": True},
    )
    if resp.status_code == 422:
        pytest.fail(
            "Sidecar rejected the `coordinate` grant verb at the schema level — "
            "RFC-0007 amendment set adds it alongside `messaging`."
        )
