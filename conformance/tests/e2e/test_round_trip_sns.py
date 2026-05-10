# RFC-0005 — round-trip across SNS implementations

"""Resolve the same shadowname via --target sns and --peer-target sns; assert the records agree."""

from __future__ import annotations

import pytest
from shadownet.crypto.jwt import decode_unverified_claims

pytestmark = [pytest.mark.class_("sns"), pytest.mark.round_trip("sns")]


@pytest.mark.network
@pytest.mark.rfc("0005", section="RoundTrip", requirement="record_agreement_across_impls")
async def test_record_agrees_across_two_sns_impls(sns_url, peer_sns_url, http, conformance_config):
    """Two SNS impls hosting the same provider MUST return semantically identical records.

    Both URLs resolve the same shadowname; we compare the inner record's
    `did`, `endpoint`, `publicKey`, and `subjectType`. `iat`/`exp`/sigs may
    legitimately differ between impls (each signs with its own clock).
    """
    name = conformance_config.sns_test_shadowname
    if not name:
        pytest.skip("no --sns-test-shadowname configured for round-trip resolve")

    async def fetch(url: str) -> dict[str, object]:
        resp = await http.get(
            f"{url}/.well-known/sns/v1/resolve",
            params={"name": name},
            headers={"Accept": "application/jwt"},
        )
        assert resp.status_code == 200, f"{url}: {resp.status_code}"
        return decode_unverified_claims(resp.text.strip())["record"]

    a = await fetch(sns_url)
    b = await fetch(peer_sns_url)
    for field in ("did", "endpoint", "subjectType", "publicKey", "shadowname"):
        assert a.get(field) == b.get(field), (
            f"records disagree on {field}: A={a.get(field)!r} B={b.get(field)!r}"
        )
