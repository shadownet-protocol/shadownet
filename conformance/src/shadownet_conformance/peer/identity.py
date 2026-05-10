"""Deterministic identity for the in-process A2A test peer.

The peer's keypair is derived from the `peer_holder` seed in
`fixtures/seeds.toml`. Its credential and freshness proof are committed
fixtures issued by the canonical SCA. A Sidecar-under-test that trusts
`did:web:sca.sh4dow.org` will accept the peer's VP.
"""

from __future__ import annotations

import secrets
from functools import cache

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.key import derive_did_key

from shadownet_conformance.fixtures import load_jwt

# Must match fixtures/seeds.toml `peer_holder.hex`.
PEER_SEED_HEX = "3030303030303030303030303030303030303030303030303030303030303030"

PEER_CREDENTIAL_PATH = "credentials/peer_l2.jwt"
PEER_FRESHNESS_PATH = "freshness/peer_l2_fresh.jwt"


class PeerIdentity:
    """The peer's stable identity + cached fixture artifacts."""

    def __init__(self) -> None:
        self._keypair = Ed25519KeyPair.from_seed(bytes.fromhex(PEER_SEED_HEX))
        self._did = derive_did_key(bytes(self._keypair.public_key.public_bytes_raw()))
        self._webhook_secret = secrets.token_hex(32)

    @property
    def keypair(self) -> Ed25519KeyPair:
        return self._keypair

    @property
    def did(self) -> str:
        return self._did

    @property
    def kid(self) -> str:
        return f"{self._did}#key-1"

    @property
    def webhook_secret(self) -> str:
        return self._webhook_secret

    @cache  # noqa: B019 — peer is process-singleton; cache is intentional
    def credential_jwt(self) -> str:
        return load_jwt(PEER_CREDENTIAL_PATH)

    @cache  # noqa: B019
    def freshness_jwt(self) -> str:
        return load_jwt(PEER_FRESHNESS_PATH)
