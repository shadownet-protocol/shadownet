# RFC-0005, RFC-0002 §did:web — organizations

"""SNS provider MUST publish a did:web document at /.well-known/did.json."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.class_("sns")


@pytest.mark.network
@pytest.mark.rfc("0005", section="DIDDocument", requirement="endpoint_present")
async def test_well_known_did_json_returns_200(sns_url, http):
    resp = await http.get(f"{sns_url}/.well-known/did.json")
    assert resp.status_code == 200


@pytest.mark.network
@pytest.mark.rfc("0005", section="DIDDocument", requirement="document_shape")
async def test_well_known_did_json_has_required_fields(sns_url, http):
    body = (await http.get(f"{sns_url}/.well-known/did.json")).json()
    assert isinstance(body.get("id"), str)
    assert body["id"].startswith("did:web:")
    assert isinstance(body.get("verificationMethod"), list)
    assert body["verificationMethod"], "verificationMethod must be non-empty"
