# RFC-0004 §Policy document

"""SCA MUST publish its issuance policy at /.well-known/sca/policy.json."""

from __future__ import annotations

import pytest
from shadownet.sca.policy import SCAPolicy

pytestmark = pytest.mark.class_("sca")


@pytest.mark.network
@pytest.mark.rfc("0004", section="Policy", requirement="endpoint_present")
async def test_policy_endpoint_returns_200(sca_url, http):
    resp = await http.get(f"{sca_url}/.well-known/sca/policy.json")
    assert resp.status_code == 200


@pytest.mark.network
@pytest.mark.rfc("0004", section="Policy", requirement="parses_into_model")
async def test_policy_parses_into_model(sca_url, http):
    resp = await http.get(f"{sca_url}/.well-known/sca/policy.json")
    SCAPolicy.model_validate(resp.json())


@pytest.mark.network
@pytest.mark.rfc("0004", section="Policy", requirement="version_field_present")
async def test_policy_carries_shadownet_version(sca_url, http):
    body = (await http.get(f"{sca_url}/.well-known/sca/policy.json")).json()
    assert body.get("shadownet:v") == "0.1"


@pytest.mark.network
@pytest.mark.rfc("0004", section="Policy", requirement="issuer_matches_did")
async def test_policy_issuer_matches_did_document(sca_url, http):
    did_doc = (await http.get(f"{sca_url}/.well-known/did.json")).json()
    policy = (await http.get(f"{sca_url}/.well-known/sca/policy.json")).json()
    assert policy["issuer"] == did_doc["id"], "policy.issuer must equal the DID document id"


@pytest.mark.network
@pytest.mark.rfc("0004", section="Policy", requirement="declares_at_least_one_level")
async def test_policy_offers_at_least_one_level(sca_url, http):
    policy = (await http.get(f"{sca_url}/.well-known/sca/policy.json")).json()
    levels = policy.get("levels", [])
    assert len(levels) >= 1, "policy.levels must offer at least one level"
    for level in levels:
        assert isinstance(level.get("level"), str)
        assert level["level"].startswith("urn:")
        assert isinstance(level.get("method"), str)
        assert isinstance(level.get("credentialLifetimeDays"), int)
        # RFC-0003 SHOULD: credential lifetime ≤ 90 days for the L*-tier
        # (organizational credentials may carry longer lifetimes per
        # individual SCA policy; we assert the L1/L2/L3 cap separately).
        if level["level"] in {
            "urn:shadownet:level:L1",
            "urn:shadownet:level:L2",
            "urn:shadownet:level:L3",
        }:
            assert level["credentialLifetimeDays"] <= 90, (
                f"L-tier credential lifetime {level['credentialLifetimeDays']}d exceeds 90d"
            )


@pytest.mark.network
@pytest.mark.rfc("0004", section="Policy", requirement="freshness_window_present")
async def test_policy_declares_freshness_window(sca_url, http):
    policy = (await http.get(f"{sca_url}/.well-known/sca/policy.json")).json()
    assert isinstance(policy.get("freshnessWindowSeconds"), int)
    assert policy["freshnessWindowSeconds"] > 0
