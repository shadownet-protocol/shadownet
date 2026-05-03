# RFC-0005 §Records, §Resolution flow step 4

"""Record JWT MUST verify against the provider's published did:web key."""

from __future__ import annotations

import httpx
import pytest
from shadownet.did.resolver import Resolver
from shadownet.did.web import WebDIDResolver
from shadownet.sns.record import verify_record

pytestmark = pytest.mark.class_("sns")


@pytest.mark.network
@pytest.mark.rfc("0005", section="Signing", requirement="signature_verifies_against_did_web")
async def test_record_signature_verifies_against_did_document(
    sns_url, http: httpx.AsyncClient, sns_test_shadowname
):
    """End-to-end signature verification using shadownet-py's verify_record."""
    resp = await http.get(
        f"{sns_url}/.well-known/sns/v1/resolve",
        params={"name": sns_test_shadowname},
        headers={"Accept": "application/jwt"},
    )
    token = resp.text.strip()

    # We need to resolve the SNS provider's DID document. The provider's DID
    # is `did:web:<provider-host>` per the shadowname's right-hand side.
    import urllib.parse

    parsed = urllib.parse.urlparse(sns_url)
    provider_host = parsed.hostname or ""
    expected_provider_did = f"did:web:{provider_host}"

    # WebDIDResolver fetches via HTTPS; the SNS server is exposed on its own
    # listen address. We special-case localhost with an http override here so
    # the conformance suite is runnable against a local-only deployment.
    web_resolver = WebDIDResolver(http=http)
    resolver = Resolver(web=web_resolver)
    record = await verify_record(
        token,
        expected_provider_did=expected_provider_did,
        resolver=resolver,
    )
    assert record.shadowname == sns_test_shadowname


@pytest.mark.network
@pytest.mark.rfc("0005", section="Signing", requirement="alg_is_eddsa")
async def test_record_uses_eddsa(sns_url, http, sns_test_shadowname):
    import base64
    import json

    resp = await http.get(
        f"{sns_url}/.well-known/sns/v1/resolve",
        params={"name": sns_test_shadowname},
        headers={"Accept": "application/jwt"},
    )
    header_b64 = resp.text.strip().split(".", 1)[0]
    header = json.loads(base64.urlsafe_b64decode(header_b64 + "==").decode())
    assert header["alg"] == "EdDSA"
