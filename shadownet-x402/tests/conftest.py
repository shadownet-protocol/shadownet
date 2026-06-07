from __future__ import annotations

import time

import pytest
from shadownet.credential import CredentialPayload, RevocationPointer, mint_credential
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.identifiers import encode_public_key


@pytest.fixture
def issuer_key() -> Ed25519KeyPair:
    return Ed25519KeyPair.generate()


@pytest.fixture
def issuer_pk(issuer_key: Ed25519KeyPair) -> str:
    return encode_public_key(issuer_key.public_bytes)


@pytest.fixture
def buyer_key() -> Ed25519KeyPair:
    return Ed25519KeyPair.generate()


@pytest.fixture
def buyer_sub(buyer_key: Ed25519KeyPair) -> str:
    return encode_public_key(buyer_key.public_bytes)


@pytest.fixture
def credential_jws(issuer_key: Ed25519KeyPair, buyer_sub: str) -> str:
    now = int(time.time())
    payload = CredentialPayload(
        iss="acme.example",
        sub=buyer_sub,
        kind="org_affiliation",
        org="acme.example",
        iat=now,
        exp=now + 3600,
        rev=RevocationPointer(epoch="2026q2", idx=0),
    )
    return mint_credential(payload, issuer_key)
