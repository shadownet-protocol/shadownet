"""In-process A2A test peer used as the other side of Sidecar handshakes."""

from shadownet_conformance.peer.identity import PeerIdentity
from shadownet_conformance.peer.server import (
    A2AReceived,
    Peer,
    PeerHandle,
    spawn_peer,
)

__all__ = [
    "A2AReceived",
    "Peer",
    "PeerHandle",
    "PeerIdentity",
    "spawn_peer",
]
