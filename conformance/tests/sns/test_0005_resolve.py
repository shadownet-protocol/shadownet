# RFC-0005 §Resolution endpoint

"""SNS provider MUST resolve known shadownames and 404/410 unknown / tombstoned ones."""

from __future__ import annotations

import pytest
from shadownet.crypto.jwt import decode_unverified_claims
from shadownet.sns.record import parse_shadowname

pytestmark = pytest.mark.class_("sns")


@pytest.mark.network
@pytest.mark.rfc("0005", section="Resolve", requirement="happy_path")
async def test_resolve_returns_signed_record(sns_url, http, sns_test_shadowname):
    """Resolving a registered shadowname MUST return a signed JWT record."""
    resp = await http.get(
        f"{sns_url}/.well-known/sns/v1/resolve",
        params={"name": sns_test_shadowname},
        headers={"Accept": "application/jwt"},
    )
    assert resp.status_code == 200
    body = resp.text.strip()
    parts = body.split(".")
    assert len(parts) == 3, "response must be a compact JWS"
    claims = decode_unverified_claims(body)
    assert claims["sub"] == sns_test_shadowname
    assert claims["shadownet:v"] == "0.1"
    assert claims["iss"].startswith("did:web:")
    record = claims["record"]
    assert record["shadowname"] == sns_test_shadowname
    assert record["did"].startswith("did:")
    assert isinstance(record["ttl"], int) and record["ttl"] > 0
    assert record["publicKey"]["kty"] == "OKP"
    assert record["publicKey"]["crv"] == "Ed25519"


@pytest.mark.network
@pytest.mark.rfc("0005", section="Resolve", requirement="unknown_returns_404")
async def test_resolve_unknown_shadowname_returns_404(sns_url, http):
    """Unknown shadownames MUST return 404."""
    # Use a deterministically-implausible local-part on the same provider.
    # We derive provider from the SNS URL's host, falling back to example.test.
    import urllib.parse

    parsed = urllib.parse.urlparse(sns_url)
    provider = parsed.hostname or "example.test"
    bogus = f"shadownet-conformance-bogus-name-zzz999@{provider}"
    resp = await http.get(
        f"{sns_url}/.well-known/sns/v1/resolve",
        params={"name": bogus},
        headers={"Accept": "application/jwt"},
    )
    assert resp.status_code == 404, f"unknown name should be 404, got {resp.status_code}"


@pytest.mark.network
@pytest.mark.rfc("0005", section="Resolve", requirement="ttl_within_bounds")
async def test_resolved_ttl_within_rfc_bounds(sns_url, http, sns_test_shadowname):
    """RFC-0005: ttl SHOULD be ≥ 60s and ≤ 86400s; exp - iat MUST equal ttl."""
    resp = await http.get(
        f"{sns_url}/.well-known/sns/v1/resolve",
        params={"name": sns_test_shadowname},
        headers={"Accept": "application/jwt"},
    )
    claims = decode_unverified_claims(resp.text.strip())
    ttl = claims["record"]["ttl"]
    assert 60 <= ttl <= 86400, f"ttl {ttl} outside RFC bounds [60, 86400]"
    assert claims["exp"] - claims["iat"] == ttl, (
        f"exp - iat ({claims['exp'] - claims['iat']}) MUST equal record.ttl ({ttl})"
    )


@pytest.mark.network
@pytest.mark.rfc("0005", section="Resolve", requirement="record_shadowname_canonical")
async def test_record_shadowname_is_canonical(sns_url, http, sns_test_shadowname):
    """Record.shadowname MUST match the requested name (case-insensitive on local-part)."""
    resp = await http.get(
        f"{sns_url}/.well-known/sns/v1/resolve",
        params={"name": sns_test_shadowname},
        headers={"Accept": "application/jwt"},
    )
    record = decode_unverified_claims(resp.text.strip())["record"]
    local_in, provider_in = parse_shadowname(sns_test_shadowname)
    local_out, provider_out = parse_shadowname(record["shadowname"])
    assert local_in.lower() == local_out.lower()
    assert provider_in == provider_out
