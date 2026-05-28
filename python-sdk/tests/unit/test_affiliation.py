from __future__ import annotations

import time
from typing import cast

import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.document import DIDDocument
from shadownet.did.key import derive_did_key
from shadownet.did.resolver import Resolver
from shadownet.vc.affiliation import (
    decode_affiliation_credential,
    issue_affiliation_credential,
    new_affiliation_credential,
    verify_affiliation_credential,
)
from shadownet.vc.errors import CredentialInvalid


def _did_web_doc(did: str, kp: Ed25519KeyPair, delegated: list[str] | None = None) -> DIDDocument:
    return DIDDocument.model_validate(
        {
            "id": did,
            "verificationMethod": [
                {
                    "id": f"{did}#k1",
                    "type": "JsonWebKey2020",
                    "controller": did,
                    "publicKeyJwk": kp.public_jwk(),
                }
            ],
            "authentication": [f"{did}#k1"],
            "assertionMethod": [f"{did}#k1"],
            **({"shadownet:delegatedIssuers": delegated} if delegated is not None else {}),
        }
    )


class _StubResolver(Resolver):
    def __init__(self, docs: dict[str, DIDDocument]) -> None:
        super().__init__(web=None)
        self._docs = docs

    async def resolve(self, did: str) -> DIDDocument:
        if did in self._docs:
            return self._docs[did]
        return await super().resolve(did)


@pytest.fixture
def org_fixture() -> dict[str, object]:
    org_kp = Ed25519KeyPair.generate()
    org_did = "did:web:acme.example"
    sca_kp = Ed25519KeyPair.generate()
    sca_did = "did:web:sca.acme.example"
    employee_kp = Ed25519KeyPair.generate()
    employee_did = derive_did_key(employee_kp.public_bytes)
    org_doc = _did_web_doc(org_did, org_kp, delegated=[sca_did])
    sca_doc = _did_web_doc(sca_did, sca_kp)
    resolver = _StubResolver({org_did: org_doc, sca_did: sca_doc})
    return {
        "org_kp": org_kp,
        "org_did": org_did,
        "sca_kp": sca_kp,
        "sca_did": sca_did,
        "employee_kp": employee_kp,
        "employee_did": employee_did,
        "resolver": resolver,
    }


async def test_affiliation_round_trip_direct_issuer(org_fixture: dict[str, object]) -> None:
    f = org_fixture
    cred = new_affiliation_credential(
        issuer=cast("str", f["org_did"]),
        subject=cast("str", f["employee_did"]),
        affiliation=cast("str", f["org_did"]),
        role="member",
        groups=["engineering"],
        lifetime_seconds=7 * 24 * 3600,
    )
    token = issue_affiliation_credential(
        issuer_key=cast("Ed25519KeyPair", f["org_kp"]),
        issuer_kid=f"{cast('str', f['org_did'])}#k1",
        credential=cred,
    )
    got = await verify_affiliation_credential(
        token, resolver=cast("Resolver", f["resolver"]), now=int(time.time())
    )
    assert got.affiliation == f["org_did"]
    assert got.role == "member"
    assert got.groups == ["engineering"]


async def test_affiliation_round_trip_delegated_sca(org_fixture: dict[str, object]) -> None:
    f = org_fixture
    cred = new_affiliation_credential(
        issuer=cast("str", f["sca_did"]),
        subject=cast("str", f["employee_did"]),
        affiliation=cast("str", f["org_did"]),
        lifetime_seconds=7 * 24 * 3600,
    )
    token = issue_affiliation_credential(
        issuer_key=cast("Ed25519KeyPair", f["sca_kp"]),
        issuer_kid=f"{cast('str', f['sca_did'])}#k1",
        credential=cred,
    )
    got = await verify_affiliation_credential(
        token, resolver=cast("Resolver", f["resolver"]), now=int(time.time())
    )
    assert got.iss == f["sca_did"]
    assert got.affiliation == f["org_did"]


async def test_affiliation_rejects_unauthorized_issuer() -> None:
    org_kp = Ed25519KeyPair.generate()
    org_did = "did:web:acme.example"
    rogue_kp = Ed25519KeyPair.generate()
    rogue_did = "did:web:rogue.example"
    employee_kp = Ed25519KeyPair.generate()
    employee_did = derive_did_key(employee_kp.public_bytes)
    resolver = _StubResolver(
        {
            org_did: _did_web_doc(org_did, org_kp, delegated=[]),
            rogue_did: _did_web_doc(rogue_did, rogue_kp),
        }
    )
    cred = new_affiliation_credential(
        issuer=rogue_did,
        subject=employee_did,
        affiliation=org_did,
        lifetime_seconds=3600,
    )
    token = issue_affiliation_credential(
        issuer_key=rogue_kp, issuer_kid=f"{rogue_did}#k1", credential=cred
    )
    with pytest.raises(CredentialInvalid):
        await verify_affiliation_credential(token, resolver=resolver, now=int(time.time()))


def test_affiliation_construction_rejects_overlong_lifetime() -> None:
    with pytest.raises(ValueError):
        new_affiliation_credential(
            issuer="did:web:acme.example",
            subject="did:key:z6MkrJVnaZkeFzdQyMZu1cgjg7k1pZZ6pvBQ7XJPt4swbTQ2",
            affiliation="did:web:acme.example",
            lifetime_seconds=60 * 24 * 3600,
        )


def test_affiliation_decode_round_trip() -> None:
    org_kp = Ed25519KeyPair.generate()
    org_did = "did:web:acme.example"
    employee_kp = Ed25519KeyPair.generate()
    employee_did = derive_did_key(employee_kp.public_bytes)
    cred = new_affiliation_credential(
        issuer=org_did,
        subject=employee_did,
        affiliation=org_did,
        role="admin",
        lifetime_seconds=3600,
    )
    token = issue_affiliation_credential(
        issuer_key=org_kp, issuer_kid=f"{org_did}#k1", credential=cred
    )
    decoded = decode_affiliation_credential(token)
    assert decoded.role == "admin"
    assert decoded.affiliation == org_did
