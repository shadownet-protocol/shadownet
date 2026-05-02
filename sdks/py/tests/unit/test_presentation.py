from __future__ import annotations

import time

import httpx
import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.key import derive_did_key
from shadownet.did.resolver import Resolver
from shadownet.trust import TrustStore
from shadownet.vc.credential import CredentialStatus, issue_credential, new_credential
from shadownet.vc.errors import PresentationInvalid
from shadownet.vc.freshness import DEFAULT_FRESHNESS_WINDOW_SECONDS, mint_freshness_proof
from shadownet.vc.presentation import mint_presentation, verify_presentation
from shadownet.vc.status_list import StatusListClient, encode_bitstring


@pytest.fixture
def issuer() -> tuple[Ed25519KeyPair, str]:
    kp = Ed25519KeyPair.generate()
    return kp, derive_did_key(kp.public_bytes)


@pytest.fixture
def subject() -> tuple[Ed25519KeyPair, str]:
    kp = Ed25519KeyPair.generate()
    return kp, derive_did_key(kp.public_bytes)


@pytest.fixture
def verifier() -> str:
    return derive_did_key(Ed25519KeyPair.generate().public_bytes)


def _build_credential(
    issuer_kp, issuer_did, subject_did, *, level="urn:shadownet:level:L1", age=0, status=None
):
    cred = new_credential(
        issuer=issuer_did,
        subject=subject_did,
        level=level,
        subject_type="person",
        issued_at=int(time.time()) - age,
        status=status,
    )
    token = issue_credential(issuer_key=issuer_kp, issuer_kid=issuer_did, credential=cred)
    return cred, token


async def test_mint_and_verify_presentation_round_trip(issuer, subject, verifier) -> None:
    issuer_kp, issuer_did = issuer
    subject_kp, subject_did = subject
    _, cred_jwt = _build_credential(issuer_kp, issuer_did, subject_did)
    vp_jwt = mint_presentation(
        holder_key=subject_kp,
        holder_did=subject_did,
        audience_did=verifier,
        credentials=[cred_jwt],
    )
    result = await verify_presentation(
        vp_jwt,
        resolver=Resolver(),
        expected_audience=verifier,
    )
    assert result.holder_did == subject_did
    assert len(result.credentials) == 1
    assert result.credentials[0].iss == issuer_did


async def test_audience_mismatch_rejected(issuer, subject, verifier) -> None:
    issuer_kp, issuer_did = issuer
    subject_kp, subject_did = subject
    _, cred_jwt = _build_credential(issuer_kp, issuer_did, subject_did)
    vp_jwt = mint_presentation(
        holder_key=subject_kp,
        holder_did=subject_did,
        audience_did="did:key:z6MkOther",
        credentials=[cred_jwt],
    )
    with pytest.raises(PresentationInvalid):
        await verify_presentation(vp_jwt, resolver=Resolver(), expected_audience=verifier)


async def test_nonce_mismatch_rejected(issuer, subject, verifier) -> None:
    issuer_kp, issuer_did = issuer
    subject_kp, subject_did = subject
    _, cred_jwt = _build_credential(issuer_kp, issuer_did, subject_did)
    vp_jwt = mint_presentation(
        holder_key=subject_kp,
        holder_did=subject_did,
        audience_did=verifier,
        credentials=[cred_jwt],
        nonce="alpha",
    )
    with pytest.raises(PresentationInvalid):
        await verify_presentation(
            vp_jwt,
            resolver=Resolver(),
            expected_audience=verifier,
            expected_nonce="beta",
        )


async def test_subject_holder_mismatch_rejected(issuer, subject, verifier) -> None:
    issuer_kp, issuer_did = issuer
    subject_kp, subject_did = subject
    other_did = derive_did_key(Ed25519KeyPair.generate().public_bytes)
    # Credential issued for `other_did` but holder is `subject_did` — should fail (RFC-0006 step 3.b).
    _, cred_jwt = _build_credential(issuer_kp, issuer_did, other_did)
    vp_jwt = mint_presentation(
        holder_key=subject_kp,
        holder_did=subject_did,
        audience_did=verifier,
        credentials=[cred_jwt],
    )
    with pytest.raises(PresentationInvalid):
        await verify_presentation(vp_jwt, resolver=Resolver(), expected_audience=verifier)


async def test_freshness_required_for_old_credential(issuer, subject, verifier) -> None:
    issuer_kp, issuer_did = issuer
    subject_kp, subject_did = subject
    cred, cred_jwt = _build_credential(
        issuer_kp,
        issuer_did,
        subject_did,
        age=DEFAULT_FRESHNESS_WINDOW_SECONDS + 600,
    )
    # No freshness proof attached — should fail.
    vp_jwt = mint_presentation(
        holder_key=subject_kp,
        holder_did=subject_did,
        audience_did=verifier,
        credentials=[cred_jwt],
    )
    with pytest.raises(PresentationInvalid):
        await verify_presentation(vp_jwt, resolver=Resolver(), expected_audience=verifier)

    # With a proof — succeeds.
    proof_jwt = mint_freshness_proof(
        issuer_key=issuer_kp,
        issuer_did=issuer_did,
        issuer_kid=issuer_did,
        credential_jti=cred.jti,
    )
    vp_jwt = mint_presentation(
        holder_key=subject_kp,
        holder_did=subject_did,
        audience_did=verifier,
        credentials=[cred_jwt],
        freshness_proofs=[proof_jwt],
    )
    result = await verify_presentation(vp_jwt, resolver=Resolver(), expected_audience=verifier)
    assert len(result.credentials) == 1


async def test_status_check_failclosed_above_l1(issuer, subject, verifier) -> None:
    issuer_kp, issuer_did = issuer
    subject_kp, subject_did = subject
    status = CredentialStatus(
        statusListIndex="0",
        statusListCredential="https://sca.example/status/x",
    )
    _, cred_jwt = _build_credential(
        issuer_kp,
        issuer_did,
        subject_did,
        level="urn:shadownet:level:L2",
        status=status,
    )
    vp_jwt = mint_presentation(
        holder_key=subject_kp,
        holder_did=subject_did,
        audience_did=verifier,
        credentials=[cred_jwt],
    )
    # No status_list_client supplied → must fail closed for L2.
    with pytest.raises(PresentationInvalid):
        await verify_presentation(vp_jwt, resolver=Resolver(), expected_audience=verifier)


async def test_trust_store_drops_untrusted_credential(issuer, subject, verifier) -> None:
    issuer_kp, issuer_did = issuer
    subject_kp, subject_did = subject
    _, cred_jwt = _build_credential(
        issuer_kp, issuer_did, subject_did, level="urn:shadownet:level:L2"
    )
    vp_jwt = mint_presentation(
        holder_key=subject_kp,
        holder_did=subject_did,
        audience_did=verifier,
        credentials=[cred_jwt],
    )
    trust_store = TrustStore.from_pairs(
        [("did:web:other-issuer.example", ["urn:shadownet:level:L2"])]
    )
    result = await verify_presentation(
        vp_jwt,
        resolver=Resolver(),
        expected_audience=verifier,
        trust_store=trust_store,
    )
    # Credential from issuer_did is dropped — trust store doesn't list it.
    assert result.credentials == ()


async def test_status_check_not_revoked(issuer, subject, verifier) -> None:
    issuer_kp, issuer_did = issuer
    subject_kp, subject_did = subject
    status = CredentialStatus(
        statusListIndex="3",
        statusListCredential="https://sca.example/status/x",
    )
    _, cred_jwt = _build_credential(
        issuer_kp,
        issuer_did,
        subject_did,
        level="urn:shadownet:level:L2",
        status=status,
    )
    vp_jwt = mint_presentation(
        holder_key=subject_kp,
        holder_did=subject_did,
        audience_did=verifier,
        credentials=[cred_jwt],
    )

    # Build a status list where bit 3 is NOT set.
    encoded = encode_bitstring(bytes(64))
    status_jwt = _build_signed_status_credential(encoded)

    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=status_jwt))
    async with httpx.AsyncClient(transport=transport) as http:
        client = StatusListClient(http)
        result = await verify_presentation(
            vp_jwt,
            resolver=Resolver(),
            expected_audience=verifier,
            status_list_client=client,
        )
    assert len(result.credentials) == 1


def _build_signed_status_credential(encoded: str) -> str:
    from shadownet.crypto.jwt import sign_jwt as _sign

    payload = {
        "iss": "did:web:sca.example",
        "iat": 1,
        "exp": 9999999999,
        "vc": {
            "@context": ["https://www.w3.org/ns/credentials/v2"],
            "type": ["VerifiableCredential", "BitstringStatusListCredential"],
            "credentialSubject": {
                "id": "https://sca.example/status/x",
                "type": "BitstringStatusList",
                "statusPurpose": "revocation",
                "encodedList": encoded,
            },
        },
    }
    return _sign(payload, Ed25519KeyPair.generate())
