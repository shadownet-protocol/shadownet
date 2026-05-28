"""Cost-guarantee type-level guard: any handler that implements the routing
boundary MUST surface a RoutingDecision so the Sidecar's classifier — not a
host-LLM call — picks the destination.

Per RFC-0006 §Cost guarantee, only the Inbox route may invoke the host
agent's reasoning loop. This test asserts that the SDK keeps the three
permitted decisions distinct and that the Sidecar Protocol exposes the
quarantine and contact-profile tools as separate methods (so existing
inbox-message-only Sidecars don't accidentally satisfy the runtime-checkable
Protocol and fall back to delivering quarantined items as inbox events).
"""

from __future__ import annotations

import inspect

from shadownet.a2a import ROUTE_DROP, ROUTE_INBOX, ROUTE_QUARANTINE, RoutingDecision
from shadownet.mcp.protocol import Sidecar


def test_routing_decision_has_three_distinct_values() -> None:
    distinct = {ROUTE_INBOX, ROUTE_QUARANTINE, ROUTE_DROP}
    assert len(distinct) == 3
    # The Literal type accepts exactly these three strings.
    assert ROUTE_INBOX in RoutingDecision.__args__  # type: ignore[attr-defined]
    assert ROUTE_QUARANTINE in RoutingDecision.__args__  # type: ignore[attr-defined]
    assert ROUTE_DROP in RoutingDecision.__args__  # type: ignore[attr-defined]


def test_sidecar_protocol_exposes_quarantine_tools() -> None:
    members = {name for name, _ in inspect.getmembers(Sidecar)}
    required = {
        "social_quarantine_list",
        "social_quarantine_review",
        "social_set_contact_profile",
    }
    missing = required - members
    assert not missing, (
        f"Sidecar Protocol is missing the new RFC-0007 tools: {sorted(missing)}. "
        "Cloud/local implementations must implement them to satisfy the protocol."
    )


def test_sidecar_protocol_keeps_inbox_and_quarantine_separate() -> None:
    # The cost guarantee depends on these being distinct surfaces. If a future
    # refactor merges them, the host agent could legitimately call inbox_wait
    # and receive items that should have stayed in quarantine.
    members = {name for name, _ in inspect.getmembers(Sidecar)}
    assert "social_inbox" in members
    assert "social_inbox_wait" in members
    assert "social_quarantine_list" in members
