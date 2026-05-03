from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from shadownet_conformance.config import Role
from shadownet_conformance.peer import spawn_peer

if TYPE_CHECKING:
    from collections.abc import Iterator

    from shadownet_conformance.config import Config
    from shadownet_conformance.peer import PeerHandle


@pytest.fixture(scope="session")
def sidecar_url(conformance_config: Config) -> str:
    url = conformance_config.target(Role.SIDECAR)
    if url is None:
        pytest.skip("no --target sidecar configured")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def peer(conformance_config: Config) -> Iterator[PeerHandle]:
    handle = spawn_peer(host=conformance_config.peer_listen_host)
    try:
        yield handle
    finally:
        handle.stop()


@pytest.fixture(autouse=True)
def _reset_peer_each_test(peer: PeerHandle) -> None:
    peer.peer.reset()


@pytest.fixture
async def http(conformance_config: Config) -> Iterator[httpx.AsyncClient]:
    timeout = httpx.Timeout(conformance_config.http_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        yield client


@pytest.fixture
async def sidecar_did(sidecar_url: str, http: httpx.AsyncClient) -> str:
    """Resolve the sidecar's DID via its agent card.

    The agent card MUST list the sidecar's DID per RFC-0006 §Required A2A surface.
    """
    resp = await http.get(f"{sidecar_url}/.well-known/agent-card.json")
    if resp.status_code != 200:
        pytest.fail(
            f"sidecar agent card not reachable: GET /.well-known/agent-card.json -> "
            f"{resp.status_code}"
        )
    card = resp.json()
    if not isinstance(card.get("did"), str):
        pytest.fail("sidecar agent card missing required 'did' field (RFC-0006)")
    return card["did"]
