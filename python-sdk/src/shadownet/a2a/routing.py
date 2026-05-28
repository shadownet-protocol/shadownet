from __future__ import annotations

from typing import Literal

# RFC-0006 §Routing and quarantine. Only RouteInbox is permitted to invoke the
# host agent's reasoning loop downstream of the Sidecar; RouteQuarantine holds
# the item for the Subject's user-driven review; RouteDrop short-circuits with
# an error response. The Sidecar's rules-only classifier picks the route
# before the host agent is consulted.
RoutingDecision = Literal["inbox", "quarantine", "drop"]

ROUTE_INBOX: RoutingDecision = "inbox"
ROUTE_QUARANTINE: RoutingDecision = "quarantine"
ROUTE_DROP: RoutingDecision = "drop"

__all__ = ["ROUTE_DROP", "ROUTE_INBOX", "ROUTE_QUARANTINE", "RoutingDecision"]
