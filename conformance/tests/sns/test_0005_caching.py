# RFC-0005 §Caching

"""SNS responses MUST honor caching directives per the RFC."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.class_("sns")


@pytest.mark.network
@pytest.mark.should
@pytest.mark.rfc("0005", section="Caching", requirement="negative_cache_60s_or_less")
async def test_404_response_cache_bound_or_absent(sns_url, http):
    """Negative responses SHOULD set Cache-Control with max-age ≤ 60s, or omit it entirely."""
    import urllib.parse

    parsed = urllib.parse.urlparse(sns_url)
    provider = parsed.hostname or "example.test"
    bogus = f"shadownet-conformance-cache-test-zzz@{provider}"
    resp = await http.get(
        f"{sns_url}/.well-known/sns/v1/resolve",
        params={"name": bogus},
        headers={"Accept": "application/jwt"},
    )
    assert resp.status_code == 404
    cc = resp.headers.get("cache-control", "").lower()
    if "max-age" in cc:
        # Extract `max-age=N`; allow seconds up to 60.
        for token in cc.replace(" ", "").split(","):
            if token.startswith("max-age="):
                max_age = int(token.split("=", 1)[1])
                assert max_age <= 60, f"negative cache max-age={max_age}s exceeds 60s"
