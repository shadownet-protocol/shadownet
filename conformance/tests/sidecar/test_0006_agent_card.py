# RFC-0006 §Required A2A surface

"""Sidecar MUST publish an A2A agent card with Shadownet extensions."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.class_("sidecar")


@pytest.mark.network
@pytest.mark.rfc("0006", section="AgentCard", requirement="endpoint_present")
async def test_agent_card_returns_200(sidecar_url, http):
    resp = await http.get(f"{sidecar_url}/.well-known/agent-card.json")
    assert resp.status_code == 200


@pytest.mark.network
@pytest.mark.rfc("0006", section="AgentCard", requirement="required_fields")
async def test_agent_card_lists_required_fields(sidecar_url, http):
    """RFC-0006: card MUST list name, url, did, publicKey, shadownet:v."""
    card = (await http.get(f"{sidecar_url}/.well-known/agent-card.json")).json()
    for field in ("name", "url", "did", "publicKey", "shadownet:v"):
        assert field in card, f"agent card missing required field: {field}"
    assert card["shadownet:v"] == "0.1"
    assert card["did"].startswith("did:")
    assert card["publicKey"].get("kty") == "OKP"
    assert card["publicKey"].get("crv") == "Ed25519"
