from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal

import httpx
import pytest
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.crypto.jwt import sign_jwt
from shadownet.did.key import derive_did_key
from shadownet.sca import csr as _sca_csr_mod
from shadownet.sca.client import SCAClient
from shadownet.sca.csr import CertificateSigningRequest, CSRRequest

from shadownet_conformance.config import Role
from shadownet_conformance.peer.identity import PEER_SEED_HEX

if TYPE_CHECKING:
    from collections.abc import Iterator

    from shadownet_conformance.config import Config


def _build_csr_with_kid(
    *,
    holder_key: Ed25519KeyPair,
    holder_did: str,
    sca_did: str,
    level: str,
    subject_type: Literal["person", "organization"],
    issued_at: int | None = None,
    ttl_seconds: int = 600,
) -> str:
    """CSR JWT including the spec-required `kid` header.

    shadownet-py v0.1.2's `build_csr` still omits `kid`, but RFC-0004
    §POST /issuance shows it as part of the canonical CSR header. The
    reference SCA's `verify_csr` rejects without it ("CSR kid required").
    Tracked for SDK fix in shadownet-py v0.1.3.
    """
    iat = issued_at if issued_at is not None else int(time.time())
    csr = CertificateSigningRequest(
        iss=holder_did,
        iat=iat,
        exp=iat + ttl_seconds,
        aud=sca_did,
        request=CSRRequest(level=level, subject_type=subject_type),
    )
    return sign_jwt(csr.to_claims(), holder_key, header_extras={"typ": "JWT", "kid": holder_did})


@pytest.fixture(autouse=True)
def _patch_csr_to_include_kid(monkeypatch):
    """shadownet-py v0.1.2's build_csr omits kid; SCA rejects with CSRInvalid."""
    monkeypatch.setattr(_sca_csr_mod, "build_csr", _build_csr_with_kid)
    # SCAClient imports build_csr by name — patch its module-local binding too.
    from shadownet.sca import client as _sca_client_mod

    monkeypatch.setattr(_sca_client_mod, "build_csr", _build_csr_with_kid)


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
