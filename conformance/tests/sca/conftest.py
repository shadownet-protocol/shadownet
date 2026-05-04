from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.key import derive_did_key
from shadownet.sca.client import SCAClient

from shadownet_conformance.config import Role
from shadownet_conformance.peer.identity import PEER_SEED_HEX

if TYPE_CHECKING:
    from collections.abc import Iterator

    from shadownet_conformance.config import Config


@pytest.fixture(scope="session")
def sca_url(conformance_config: Config) -> str:
    url = conformance_config.target(Role.SCA)
    if url is None:
        pytest.skip("no --target sca configured")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def proof_method_uri(conformance_config: Config) -> str:
    return conformance_config.proof_method_uri


@pytest.fixture(scope="session")
def subject_keypair() -> Ed25519KeyPair:
    """A deterministic subject identity for SCA tests.

    Uses the same seed as the in-process peer so any incidental peer↔SCA
    interaction reuses one identity.
    """
    return Ed25519KeyPair.from_seed(bytes.fromhex(PEER_SEED_HEX))


@pytest.fixture(scope="session")
def subject_did(subject_keypair: Ed25519KeyPair) -> str:
    return derive_did_key(bytes(subject_keypair.public_key.public_bytes_raw()))


@pytest.fixture
async def http(conformance_config: Config) -> Iterator[httpx.AsyncClient]:
    timeout = httpx.Timeout(conformance_config.http_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        yield client


@pytest.fixture
async def sca_client(
    sca_url: str,
    http: httpx.AsyncClient,
    subject_keypair: Ed25519KeyPair,
    subject_did: str,
) -> SCAClient:
    """An SCAClient pointed at the target SCA, signing as the subject identity.

    The SCA's own DID is fetched from `/.well-known/did.json` so the client
    can scope its subject-auth JWTs correctly.
    """
    well_known = (await http.get(f"{sca_url}/.well-known/did.json")).json()
    return SCAClient(
        http=http,
        sca_base_url=sca_url,
        sca_did=well_known["id"],
        holder_key=subject_keypair,
        holder_did=subject_did,
    )
