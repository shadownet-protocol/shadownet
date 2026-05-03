# RFC-0004 §SCA identity, RFC-0002 §did:web — organizations

"""SCA MUST publish its DID document at /.well-known/did.json."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.class_("sca")


@pytest.mark.network
@pytest.mark.rfc("0004", section="DIDDocument", requirement="endpoint_present")
async def test_well_known_did_json_returns_200(sca_url, http):
    resp = await http.get(f"{sca_url}/.well-known/did.json")
    assert resp.status_code == 200, f"GET /.well-known/did.json -> {resp.status_code}"


@pytest.mark.network
@pytest.mark.rfc("0004", section="DIDDocument", requirement="content_type_json")
async def test_well_known_did_json_is_json(sca_url, http):
    resp = await http.get(f"{sca_url}/.well-known/did.json")
    ctype = resp.headers.get("content-type", "")
    assert "application/json" in ctype or "application/did+json" in ctype, (
        f"unexpected content-type: {ctype}"
    )


@pytest.mark.network
@pytest.mark.rfc("0004", section="DIDDocument", requirement="document_shape")
async def test_well_known_did_json_has_required_fields(sca_url, http):
    """RFC-0002 §Forbidden fields: only id, verificationMethod, authentication, assertionMethod."""
    resp = await http.get(f"{sca_url}/.well-known/did.json")
    body = resp.json()
    assert isinstance(body.get("id"), str) and body["id"].startswith("did:web:")
    assert isinstance(body.get("verificationMethod"), list) and body["verificationMethod"]
    vm = body["verificationMethod"][0]
    assert vm.get("type") in {
        "Ed25519VerificationKey2020",
        "JsonWebKey2020",
        "Multikey",
    }, f"unexpected verificationMethod type: {vm.get('type')}"


@pytest.mark.network
@pytest.mark.rfc("0004", section="DIDDocument", requirement="size_limit_16k")
async def test_did_document_under_16_kib(sca_url, http):
    """RFC-0002: DID document MUST be ≤ 16 KiB."""
    resp = await http.get(f"{sca_url}/.well-known/did.json")
    assert len(resp.content) <= 16 * 1024, f"did.json is {len(resp.content)} bytes, > 16 KiB"
