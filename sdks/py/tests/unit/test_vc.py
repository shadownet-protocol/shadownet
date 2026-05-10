from __future__ import annotations

import time

import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.key import derive_did_key
from shadownet.did.resolver import Resolver
from shadownet.vc.credential import (
    CredentialStatus,
    issue_credential,
    new_credential,
    verify_credential,
)
from shadownet.vc.errors import CredentialInvalid


@pytest.fixture
def issuer() -> tuple[Ed25519KeyPair, str]:
    kp = Ed25519KeyPair.generate()
    return kp, derive_did_key(kp.public_bytes)


@pytest.fixture
def subject() -> tuple[Ed25519KeyPair, str]:
    kp = Ed25519KeyPair.generate()
    return kp, derive_did_key(kp.public_bytes)


def test_credential_round_trip_via_issuer_kid(issuer, subject) -> None:
    issuer_key, issuer_did = issuer
    _, subject_did = subject
    cred = new_credential(
        issuer=issuer_did,
        subject=subject_did,
        level="urn:shadownet:level:L1",
        subject_type="person",
    )
    token = issue_credential(
        issuer_key=issuer_key, issuer_kid=issuer_did + "#key-1", credential=cred
    )
    assert isinstance(token, str)


async def test_verify_credential_happy_path(issuer, subject) -> None:
    issuer_key, issuer_did = issuer
    _, subject_did = subject
    cred = new_credential(
        issuer=issuer_did,
        subject=subject_did,
        level="urn:shadownet:level:L2",
        subject_type="person",
    )
    token = issue_credential(issuer_key=issuer_key, issuer_kid=issuer_did, credential=cred)
    resolver = Resolver()
    verified = await verify_credential(token, resolver=resolver, now=int(time.time()))
    assert verified.iss == issuer_did
    assert verified.sub == subject_did
    assert verified.level == "urn:shadownet:level:L2"


async def test_verify_credential_signature_failure(issuer, subject) -> None:
    issuer_key, issuer_did = issuer
    _, subject_did = subject
    cred = new_credential(
        issuer=issuer_did,
        subject=subject_did,
        level="urn:shadownet:level:L1",
        subject_type="person",
    )
    token = issue_credential(issuer_key=issuer_key, issuer_kid=issuer_did, credential=cred)
    # Corrupt the JWT signature.
    parts = token.rsplit(".", 1)
    tampered = parts[0] + "." + ("A" * len(parts[1]))
    with pytest.raises(CredentialInvalid):
        await verify_credential(tampered, resolver=Resolver())


async def test_verify_credential_org_must_use_did_web(issuer) -> None:
    issuer_key, issuer_did = issuer
    # Build an organization-typed credential whose subject is a did:key. Should be rejected.
    bad_subject_kp = Ed25519KeyPair.generate()
    bad_subject_did = derive_did_key(bad_subject_kp.public_bytes)
    cred = new_credential(
        issuer=issuer_did,
        subject=bad_subject_did,
        level="urn:shadownet:level:O1",
        subject_type="organization",
    )
    token = issue_credential(issuer_key=issuer_key, issuer_kid=issuer_did, credential=cred)
    with pytest.raises(CredentialInvalid):
        await verify_credential(token, resolver=Resolver())


def test_credential_subject_id_must_match_sub(issuer, subject) -> None:
    _, issuer_did = issuer
    _, subject_did = subject
    other = derive_did_key(Ed25519KeyPair.generate().public_bytes)
    cred = new_credential(
        issuer=issuer_did,
        subject=subject_did,
        level="urn:shadownet:level:L1",
        subject_type="person",
    )
    # mutate then re-validate
    payload = cred.model_dump(by_alias=True)
    payload["vc"]["credentialSubject"]["id"] = other
    from shadownet.vc.credential import SubjectCredential

    with pytest.raises(Exception):  # noqa: B017 -- pydantic ValidationError
        SubjectCredential.model_validate(payload)


def test_credential_with_status() -> None:
    cred = new_credential(
        issuer="did:web:sca.example",
        subject="did:key:z6MkSubject",
        level="urn:shadownet:level:L2",
        subject_type="person",
        status=CredentialStatus(
            statusListIndex="42",
            statusListCredential="https://sca.example/status/2026-q3",
        ),
    )
    assert cred.status is not None
    assert cred.status.status_list_index == "42"
