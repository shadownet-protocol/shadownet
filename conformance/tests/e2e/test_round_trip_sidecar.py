# RFC-0006 — round-trip across Sidecar implementations

"""Sidecar↔Sidecar round-trip is registered now so v0.2 can light it up.

At v0.1 there is exactly one Sidecar implementation (`hermes-social`). The
test below is registered with the same `rfc`/`round_trip` markers as its
SCA/SNS siblings and skips with a single-line log explaining why. When a
second Sidecar impl exists, configure both via `--target sidecar=URL_A`
and `--peer-target sidecar=URL_B` — the test will enable itself with no
flag-design changes required.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.class_("sidecar"), pytest.mark.round_trip("sidecar")]


@pytest.mark.network
@pytest.mark.rfc("0006", section="RoundTrip", requirement="cross_sidecar_handshake")
async def test_cross_sidecar_handshake_placeholder():
    """Sidecar round-trip will run once a second v0.1 Sidecar implementation exists."""
    pytest.skip("sidecar round-trip skipped: only one v0.1 Sidecar impl exists")
