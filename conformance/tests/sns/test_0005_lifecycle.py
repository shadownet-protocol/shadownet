# RFC-0005 §Lifecycle (draft)

"""SNS servers MUST NOT serve pre-expired records and resolution MUST remain
available across the lifetime of a registered shadowname."""

from __future__ import annotations

import asyncio
import time

import pytest
from shadownet.crypto.jwt import decode_unverified_claims

pytestmark = [pytest.mark.class_("sns"), pytest.mark.draft]

# CI budget for the round-trip test: we will not sleep longer than this even
# if the served TTL is higher. Test skips with a clear reason in that case.
_MAX_WAIT_SECONDS = 120


@pytest.mark.network
@pytest.mark.rfc("0005", section="Lifecycle", requirement="exp_in_future")
async def test_resolved_record_exp_is_in_future(sns_url, http, sns_test_shadowname):
    """A served record's `exp` MUST be strictly greater than wall-clock at response time."""
    resp = await http.get(
        f"{sns_url}/.well-known/sns/v1/resolve",
        params={"name": sns_test_shadowname},
        headers={"Accept": "application/jwt"},
    )
    assert resp.status_code == 200
    claims = decode_unverified_claims(resp.text.strip())
    now = int(time.time())
    assert claims["exp"] > now, (
        f"served record's exp={claims['exp']} is not in the future (now={now}); "
        "server is returning a pre-expired record"
    )


@pytest.mark.network
@pytest.mark.rfc("0005", section="Lifecycle", requirement="resolvable_past_initial_ttl")
async def test_resolved_record_remains_resolvable_past_initial_ttl(
    sns_url, http, sns_test_shadowname
):
    """A shadowname registered once MUST stay resolvable past `iat + ttl`.

    Forces either server-side re-issuance on resolve, server-side renewal,
    or a documented client-renewal model — any conformant approach must
    leave the second resolve returning HTTP 200 with a non-expired record.
    """
    first = await http.get(
        f"{sns_url}/.well-known/sns/v1/resolve",
        params={"name": sns_test_shadowname},
        headers={"Accept": "application/jwt"},
    )
    assert first.status_code == 200
    first_claims = decode_unverified_claims(first.text.strip())
    ttl = int(first_claims["record"]["ttl"])
    if ttl > _MAX_WAIT_SECONDS:
        pytest.skip(
            f"served ttl={ttl}s exceeds CI budget ({_MAX_WAIT_SECONDS}s); "
            "operator must register a short-ttl test shadowname to exercise this requirement"
        )

    # Wait until the first envelope's exp has strictly passed.
    wait_for = max(1, first_claims["exp"] - int(time.time()) + 5)
    await asyncio.sleep(wait_for)

    second = await http.get(
        f"{sns_url}/.well-known/sns/v1/resolve",
        params={"name": sns_test_shadowname},
        headers={"Accept": "application/jwt"},
    )
    assert second.status_code == 200, (
        f"resolve after initial ttl ({ttl}s) returned {second.status_code}; "
        "server must re-issue or be backed by a renewal model"
    )
    second_claims = decode_unverified_claims(second.text.strip())
    now = int(time.time())
    assert second_claims["exp"] > now, (
        f"second resolve returned a pre-expired record (exp={second_claims['exp']}, now={now})"
    )
